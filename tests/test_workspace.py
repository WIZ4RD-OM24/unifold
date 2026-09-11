"""Tests for the generated workspace index.

The index is a derived view, so the risks are misreporting and broken links --
a table of contents that points at files which do not exist is worse than none.
"""

import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from unifold import explode as explode_mod  # noqa: E402
from unifold import workspace  # noqa: E402

SAMPLES = ROOT / "samples" / "learn-palettes"
SKIP = not SAMPLES.is_dir()
REASON = "real samples absent -- run scripts/fetch_samples.py"

LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


class WorkspaceCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="unifold-ws-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def object_dir(self, relative: str, properties=None, code=None):
        directory = self.tmp / relative
        directory.mkdir(parents=True, exist_ok=True)
        explode_mod.write_text(
            directory / "properties.txt",
            "".join("%s: %s\n" % (k, v)
                    for k, v in sorted((properties or {}).items())),
        )
        for name, text in (code or {}).items():
            explode_mod.write_text(directory / name, text)

    def small_workspace(self):
        self.object_dir(
            "expA/UFORM/COMPA",
            {"UDESCR": "First component", "ULABEL": "COMPA"},
            {"USCRIPT.proc": "entry helper\nend\ncall other(1)\n"},
        )
        self.object_dir("expA/UXGROUP/EMPLOYEE",
                        {"UFORM": "COMPA", "ULABEL": "EMPLOYEE",
                         "UBASE": "BOOTSTRAP", "U_GLAB": "EMPLOYEE"})
        self.object_dir("expB/USOURCE/MYLIB",
                        {"UDESCR": "Shared routines", "ULABEL": "MYLIB"},
                        {"UTEXT.proc": "entry other\nend\n"})
        return workspace.render(self.tmp)


class TestScanning(WorkspaceCase):
    def test_finds_objects_and_counts_code(self):
        self.small_workspace()
        objects = workspace.scan(self.tmp)
        names = sorted(o.name for o in objects)
        self.assertEqual(names, ["COMPA", "EMPLOYEE", "MYLIB"])
        compa = next(o for o in objects if o.name == "COMPA")
        self.assertEqual(compa.table, "UFORM")
        self.assertEqual(compa.export, "expA")
        self.assertEqual(compa.description, "First component")
        self.assertGreater(compa.lines, 0)

    def test_object_without_code_reports_zero_lines(self):
        self.object_dir("e/UFORM/BARE", {"ULABEL": "BARE"})
        objects = workspace.scan(self.tmp)
        self.assertEqual(objects[0].lines, 0)
        self.assertEqual(objects[0].code, [])


class TestRendering(WorkspaceCase):
    def test_lists_components_and_library_procscript(self):
        text = self.small_workspace()
        self.assertIn("## Components (`UFORM`)", text)
        self.assertIn("## Library ProcScript (`USOURCE`)", text)
        self.assertIn("COMPA", text)
        self.assertIn("MYLIB", text)

    def test_shows_descriptions(self):
        self.assertIn("First component", self.small_workspace())

    def test_reports_the_data_model(self):
        text = self.small_workspace()
        self.assertIn("## Data model", text)
        self.assertIn("BOOTSTRAP.EMPLOYEE", text)

    def test_reports_external_dependencies(self):
        # `other` is defined in expB, `call other(1)` resolves; nothing dangles.
        self.object_dir("expC/UFORM/COMPC", {"ULABEL": "COMPC"},
                        {"USCRIPT.proc": "call nowhere(1)\n"})
        text = workspace.render(self.tmp)
        self.assertIn("## External dependencies", text)
        self.assertIn("nowhere", text)

    def test_every_link_points_at_something_that_exists(self):
        self.small_workspace()
        text = workspace.render(self.tmp)
        targets = [t for t in LINK_RE.findall(text) if not t.startswith("http")]
        self.assertTrue(targets, "expected relative links")
        for target in targets:
            with self.subTest(target):
                self.assertTrue((self.tmp / target).exists(), target)

    def test_output_is_ascii_safe(self):
        # Generated output must not depend on the console's encoding.
        self.small_workspace().encode("ascii")

    def test_empty_workspace_still_renders(self):
        text = workspace.render(self.tmp)
        self.assertIn("# Workspace index", text)
        self.assertIn("| Objects | 0 |", text)


class TestWriting(WorkspaceCase):
    def test_writes_index_into_the_workspace(self):
        self.small_workspace()
        target = workspace.write(self.tmp)
        self.assertEqual(target.name, workspace.INDEX)
        self.assertIn("# Workspace index",
                      target.read_text(encoding="utf-8"))

    def test_honours_an_explicit_destination(self):
        self.small_workspace()
        target = workspace.write(self.tmp, self.tmp / "docs" / "OVERVIEW.md")
        self.assertTrue(target.is_file())

    def test_regenerating_replaces_rather_than_appends(self):
        self.small_workspace()
        first = workspace.write(self.tmp).read_text(encoding="utf-8")
        second = workspace.write(self.tmp).read_text(encoding="utf-8")
        self.assertEqual(first, second)


@unittest.skipIf(SKIP, REASON)
class TestAgainstRealExports(WorkspaceCase):
    def test_real_workspace_index_links_resolve(self):
        from unifold import explode as explode_mod

        for source in sorted(SAMPLES.glob("*.xml")):
            explode_mod.explode(source, self.tmp / source.stem, force=True)
        text = workspace.render(self.tmp)
        self.assertIn("SHOWEMPLOYEES", text)
        self.assertIn("HILIGHTROW", text)
        targets = [t for t in LINK_RE.findall(text) if not t.startswith("http")]
        missing = [t for t in targets if not (self.tmp / t).exists()]
        self.assertEqual(missing, [], "index links to files that do not exist")


if __name__ == "__main__":
    unittest.main(verbosity=2)
