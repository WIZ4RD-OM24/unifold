"""Tests for cross-referencing ProcScript.

The behaviours worth pinning are the ones that would quietly mislead: indexing
something inside a comment, missing a call because of spacing or case, or
calling a trigger "dead" when the runtime is what invokes it.
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from unifold import xref  # noqa: E402

SAMPLES = ROOT / "samples" / "learn-palettes"
SKIP = not SAMPLES.is_dir()
REASON = "real samples absent -- run scripts/fetch_samples.py"


class WorkspaceCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="unifold-xref-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def write(self, relative: str, text: str) -> None:
        path = self.tmp / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


class TestStripComment(unittest.TestCase):
    def test_removes_trailing_comment(self):
        self.assertEqual(xref.strip_comment("call foo()  ; do it").strip(),
                         "call foo()")

    def test_semicolon_inside_a_string_is_not_a_comment(self):
        line = 'message "a;b" ; real comment'
        self.assertEqual(xref.strip_comment(line).strip(), 'message "a;b"')

    def test_line_without_comment_is_unchanged(self):
        self.assertEqual(xref.strip_comment("call foo()"), "call foo()")


class TestScanning(WorkspaceCase):
    def test_finds_definitions_and_calls(self):
        self.write("exp/UFORM/COMP/USCRIPT.proc",
                   "entry doThing\n  call other(1)\nend\n"
                   "operation exec\n  activate \"SVC\".run(x)\nend\n")
        index = xref.build(self.tmp)
        self.assertEqual(index.files, 1)
        kinds = sorted((d.kind, d.name) for d in index.definitions)
        self.assertEqual(kinds, [("entry", "doThing"), ("operation", "exec")])
        names = sorted((r.kind, r.name, r.service) for r in index.references)
        self.assertEqual(names, [("activate", "run", "SVC"), ("call", "other", None)])

    def test_ignores_commented_out_code(self):
        self.write("e/T/O/A.proc", "; call ghost(1)\ncall real(2)\n")
        index = xref.build(self.tmp)
        self.assertEqual([r.name for r in index.references], ["real"])

    def test_matching_is_case_insensitive_and_space_tolerant(self):
        self.write("e/T/O/A.proc",
                   "ENTRY Thing\nCALL   thing  (1)\nACTIVATE \"S\" . Op (2)\n")
        index = xref.build(self.tmp)
        self.assertEqual([d.name for d in index.definitions], ["Thing"])
        self.assertEqual(
            sorted(r.name for r in index.references), ["Op", "thing"]
        )

    def test_records_line_numbers(self):
        self.write("e/T/O/A.proc", "\n\nentry late\n")
        index = xref.build(self.tmp)
        self.assertEqual(index.definitions[0].site.line, 3)

    def test_only_proc_files_are_scanned(self):
        self.write("e/T/O/properties.txt", "entry notCode\n")
        self.write("e/T/O/A.proc", "entry realCode\n")
        index = xref.build(self.tmp)
        self.assertEqual([d.name for d in index.definitions], ["realCode"])


class TestAnalysis(WorkspaceCase):
    def build_workspace(self):
        self.write("a/UFORM/ONE/USCRIPT.proc",
                   "entry helper\nend\n"
                   "trigger detail\n  call helper(1)\n  call missing(2)\nend\n")
        self.write("b/UFORM/TWO/USCRIPT.proc",
                   "entry orphan\nend\n"
                   "operation exec\n  call helper(3)\nend\n")
        return xref.build(self.tmp)

    def test_unresolved_lists_only_undefined_names(self):
        index = self.build_workspace()
        self.assertEqual([r.name for r in index.unresolved()], ["missing"])

    def test_uncalled_finds_the_orphan_entry(self):
        index = self.build_workspace()
        self.assertEqual([d.name for d in index.uncalled()], ["orphan"])

    def test_triggers_and_operations_are_never_called_dead(self):
        # Nothing calls `detail` or `exec` from ProcScript -- the runtime does.
        index = self.build_workspace()
        names = {d.name for d in index.uncalled()}
        self.assertNotIn("detail", names)
        self.assertNotIn("exec", names)

    def test_symbol_lookup_spans_exports(self):
        index = self.build_workspace()
        definitions, references = xref.lookup(index, "helper")
        self.assertEqual(len(definitions), 1)
        self.assertEqual(len(references), 2)
        paths = sorted(r.site.path.split("/")[0] for r in references)
        self.assertEqual(paths, ["a", "b"])

    def test_symbol_report_mentions_both_sides(self):
        text = xref.render_symbol(self.build_workspace(), "helper")
        self.assertIn("Defined in 1 place", text)
        self.assertIn("Called from 2 place", text)

    def test_unknown_symbol_is_reported_kindly(self):
        text = xref.render_symbol(self.build_workspace(), "nosuchthing")
        self.assertIn("Not found", text)

    def test_symbol_called_but_never_defined(self):
        text = xref.render_symbol(self.build_workspace(), "missing")
        self.assertIn("Defined: nowhere", text)
        self.assertIn("Called from 1 place", text)

    def test_json_is_serialisable(self):
        import json
        payload = json.loads(xref.to_json(self.build_workspace()))
        self.assertEqual(payload["unresolved"], ["missing"])
        self.assertEqual(payload["uncalled"], ["orphan"])


@unittest.skipIf(SKIP, REASON)
class TestAgainstRealExports(WorkspaceCase):
    def test_traces_a_library_proc_across_separate_exports(self):
        from unifold import explode as explode_mod

        for source in sorted(SAMPLES.glob("*.xml")):
            explode_mod.explode(source, self.tmp / source.stem, force=True)
        index = xref.build(self.tmp)
        self.assertGreater(index.files, 5)

        definitions, references = xref.lookup(index, "OccurrenceSetFieldColors")
        self.assertTrue(definitions, "library proc definition not found")
        self.assertTrue(references, "no callers found")
        # The point of the feature: callers live in exports other than the one
        # defining it.
        exports = {r.site.path.split("/")[0] for r in references}
        self.assertGreater(len(exports), 1, "expected cross-export callers")

    def test_activate_references_carry_their_service(self):
        from unifold import explode as explode_mod

        explode_mod.explode(SAMPLES / "cpt_bootstrapdsp.xml",
                            self.tmp / "dsp", force=True)
        index = xref.build(self.tmp)
        services = {r.service for r in index.references if r.kind == "activate"}
        self.assertIn("USYSSTAT", services)


if __name__ == "__main__":
    unittest.main(verbosity=2)
