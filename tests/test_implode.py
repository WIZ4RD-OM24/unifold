"""Tests for implode and the round-trip fidelity check.

The round-trip tests are the ones that matter. implode produces a file people
may import into a Uniface repository, so "it ran without crashing" is not a
useful standard -- the standard is that every element, attribute and value comes
back identical.
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from unifold import explode as explode_mod  # noqa: E402
from unifold import implode as implode_mod  # noqa: E402

SAMPLES = ROOT / "samples" / "learn-palettes"
SKIP = not SAMPLES.is_dir()
REASON = "real samples absent -- run scripts/fetch_samples.py"

MINIMAL = """<?xml version='1.0' encoding='UTF-8'?>
<UNIFACE release="9.7" xmlengine="2.0">
<TABLE>
  <DSC name="USOURCE" model="DICT"><FLD name="ULABEL" seqno="1"/><FLD name="UTEXT" seqno="2"/></DSC>
  <OCC>
    <DAT name="ULABEL">MYPROC</DAT>
    <DAT name="UEMPTY"></DAT>
    <DAT name="UTEXT" xml:space="preserve">entry foo
  return 0
end</DAT>
  </OCC>
</TABLE>
</UNIFACE>
"""


class TempCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="unifold-implode-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def source(self, text=MINIMAL) -> Path:
        path = self.tmp / "src.xml"
        path.write_text(text, encoding="utf-8")
        return path


class TestEscaping(unittest.TestCase):
    def test_markup_is_escaped(self):
        self.assertEqual(
            implode_mod.escape_text("a < b & c > d", []),
            "a &lt; b &amp; c &gt; d",
        )

    def test_placeholders_become_entity_references(self):
        self.assertEqual(
            implode_mod.escape_text("A[[uSEP]]B", ["uSEP"]),
            "A&uSEP;B",
        )

    def test_unknown_placeholders_are_left_alone(self):
        # Only entities the source file actually declared may be emitted.
        self.assertEqual(
            implode_mod.escape_text("A[[uNOPE]]B", ["uSEP"]),
            "A[[uNOPE]]B",
        )

    def test_literal_ampersand_is_not_confused_with_an_entity(self):
        # Escaping must happen before placeholder restoration, or a literal
        # ampersand would produce a bogus entity reference.
        self.assertEqual(
            implode_mod.escape_text("Tom & Jerry [[uSEP]]", ["uSEP"]),
            "Tom &amp; Jerry &uSEP;",
        )

    def test_namespaced_attribute_names_are_restored(self):
        self.assertEqual(
            implode_mod.attr_name(explode_mod.XML_NS + "space"), "xml:space"
        )
        self.assertEqual(implode_mod.attr_name("name"), "name")


class TestImplodeMechanics(TempCase):
    def test_rebuilds_a_parseable_export(self):
        tree, out = self.tmp / "tree", self.tmp / "out.xml"
        explode_mod.explode(self.source(), tree)
        result = implode_mod.implode(tree, out)
        self.assertEqual(result.warnings, [])
        self.assertTrue(out.is_file())
        data = out.read_bytes()
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"), "expected a UTF-8 BOM")
        self.assertIn(b"<DAT name=\"ULABEL\">MYPROC</DAT>", data)

    def test_preserves_empty_columns(self):
        tree, out = self.tmp / "tree", self.tmp / "out.xml"
        explode_mod.explode(self.source(), tree)
        implode_mod.implode(tree, out)
        # UEMPTY is omitted from the readable tree but must return in the XML.
        self.assertIn(b"<DAT name=\"UEMPTY\"></DAT>", out.read_bytes())

    def test_preserves_xml_space_attribute(self):
        tree, out = self.tmp / "tree", self.tmp / "out.xml"
        explode_mod.explode(self.source(), tree)
        implode_mod.implode(tree, out)
        self.assertIn(b"xml:space=\"preserve\"", out.read_bytes())

    def test_refuses_to_overwrite_without_force(self):
        tree, out = self.tmp / "tree", self.tmp / "out.xml"
        explode_mod.explode(self.source(), tree)
        out.write_text("existing", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            implode_mod.implode(tree, out)
        self.assertEqual(out.read_text(encoding="utf-8"), "existing")
        implode_mod.implode(tree, out, force=True)
        self.assertNotEqual(out.read_text(encoding="utf-8"), "existing")

    def test_requires_the_sidecar(self):
        tree, out = self.tmp / "tree", self.tmp / "out.xml"
        explode_mod.explode(self.source(), tree)
        (tree / explode_mod.SIDECAR).unlink()
        with self.assertRaises(FileNotFoundError) as caught:
            implode_mod.implode(tree, out)
        self.assertIn(explode_mod.SIDECAR, str(caught.exception))

    def test_missing_content_file_warns_rather_than_silently_dropping(self):
        tree, out = self.tmp / "tree", self.tmp / "out.xml"
        explode_mod.explode(self.source(), tree)
        (tree / "USOURCE/MYPROC/UTEXT.proc").unlink()
        result = implode_mod.implode(tree, out)
        self.assertTrue(result.warnings)
        self.assertIn("UTEXT", result.warnings[0])

    def test_properties_edited_with_windows_line_endings_stay_clean(self):
        # Editing properties.txt in a Windows editor rewrites it with CRLF. The
        # carriage return is structure, not content, and must not end up inside
        # a value and get written back into the export.
        tree, out = self.tmp / "tree", self.tmp / "out.xml"
        explode_mod.explode(self.source(), tree)
        properties = tree / "USOURCE/MYPROC/properties.txt"
        properties.write_text(
            properties.read_text(encoding="utf-8", newline="").replace("\n", "\r\n"),
            encoding="utf-8", newline="",
        )
        implode_mod.implode(tree, out, force=True)
        self.assertIn(b"<DAT name=\"ULABEL\">MYPROC</DAT>", out.read_bytes())
        self.assertNotIn(b"MYPROC\r", out.read_bytes())

    def test_edits_to_the_tree_reach_the_output(self):
        # The entire point: change a .proc file, get a changed export.
        tree, out = self.tmp / "tree", self.tmp / "out.xml"
        explode_mod.explode(self.source(), tree)
        (tree / "USOURCE/MYPROC/UTEXT.proc").write_text(
            "entry foo\n  return 42\nend", encoding="utf-8", newline=""
        )
        implode_mod.implode(tree, out)
        self.assertIn(b"return 42", out.read_bytes())


class TestPoisonedSidecar(TempCase):
    """An exploded tree is shared -- via git, review, a network drive -- so the
    sidecar is untrusted input even though explode normally writes it."""

    def setUp(self):
        super().setUp()
        self.secret = self.tmp / "secret.txt"
        self.secret.write_text("SECRET-CANARY", encoding="utf-8")
        self.tree = self.tmp / "tree"
        explode_mod.explode(self.source(), self.tree)

    def poison(self, **changes):
        import json
        path = self.tree / explode_mod.SIDECAR
        data = json.loads(path.read_text(encoding="utf-8"))
        for occ in data["occurrences"]:
            if "dir" in changes:
                occ["dir"] = changes["dir"]
            for column in occ["columns"]:
                if "file" in changes and column.get("store") == "file":
                    column["store"] = "file"
                    column["file"] = changes["file"]
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_traversal_in_a_file_reference_is_refused(self):
        self.poison(file="../../../secret.txt")
        with self.assertRaises(implode_mod.UnsafePath):
            implode_mod.implode(self.tree, self.tmp / "out.xml")

    def test_absolute_path_in_a_file_reference_is_refused(self):
        self.poison(file=str(self.secret))
        with self.assertRaises(implode_mod.UnsafePath):
            implode_mod.implode(self.tree, self.tmp / "out.xml")

    def test_traversal_in_a_directory_reference_is_refused(self):
        self.poison(dir="../..")
        with self.assertRaises(implode_mod.UnsafePath):
            implode_mod.implode(self.tree, self.tmp / "out.xml")

    def test_nothing_is_written_when_a_path_is_refused(self):
        self.poison(file="../../../secret.txt")
        target = self.tmp / "out.xml"
        with self.assertRaises(implode_mod.UnsafePath):
            implode_mod.implode(self.tree, target)
        self.assertFalse(target.exists())

    def test_the_secret_never_reaches_the_output(self):
        self.poison(file="../../../secret.txt")
        try:
            implode_mod.implode(self.tree, self.tmp / "out.xml")
        except implode_mod.UnsafePath:
            pass
        for path in self.tmp.rglob("*.xml"):
            self.assertNotIn("SECRET-CANARY",
                             path.read_text(encoding="utf-8", errors="replace"))

    def test_an_honest_tree_still_works(self):
        # The check must not break the normal path.
        result = implode_mod.implode(self.tree, self.tmp / "fine.xml")
        self.assertEqual(result.warnings, [])


class TestVerify(TempCase):
    def test_identical_documents_report_no_problems(self):
        raw = self.source().read_bytes()
        self.assertEqual(implode_mod.verify(raw, raw), [])

    def test_changed_value_is_detected(self):
        a = self.source().read_bytes()
        b = MINIMAL.replace("MYPROC", "OTHERPROC").encode("utf-8")
        problems = implode_mod.verify(a, b)
        self.assertTrue(problems)
        self.assertTrue(any("text differs" in p for p in problems))

    def test_changed_attribute_is_detected(self):
        a = self.source().read_bytes()
        b = MINIMAL.replace('release="9.7"', 'release="10.4"').encode("utf-8")
        problems = implode_mod.verify(a, b)
        self.assertTrue(any("attributes differ" in p for p in problems))

    def test_missing_element_is_detected(self):
        a = self.source().read_bytes()
        b = MINIMAL.replace('<DAT name="UEMPTY"></DAT>', "").encode("utf-8")
        self.assertTrue(implode_mod.verify(a, b))


class TestRoundTripSynthetic(TempCase):
    def test_minimal_document_round_trips(self):
        result = implode_mod.roundtrip(self.source(), self.tmp / "work")
        self.assertEqual(result.problems, [])
        self.assertEqual(result.warnings, [])
        self.assertTrue(result.ok)

    def test_repeated_table_names_do_not_duplicate_rows(self):
        # A real project export repeats the same DSC name across several TABLE
        # blocks. Grouping rows by name instead of by block duplicated every
        # occurrence; this pins the fix.
        doubled = MINIMAL.replace(
            "</UNIFACE>",
            """<TABLE>
  <DSC name="USOURCE" model="DICT"><FLD name="ULABEL" seqno="1"/></DSC>
  <OCC><DAT name="ULABEL">SECOND</DAT></OCC>
</TABLE>
</UNIFACE>""",
        )
        result = implode_mod.roundtrip(self.source(doubled), self.tmp / "work")
        self.assertEqual(result.problems, [], "rows were duplicated or lost")


@unittest.skipIf(SKIP, REASON)
class TestRoundTripRealExports(TempCase):
    def test_every_real_export_round_trips_faithfully(self):
        files = sorted(SAMPLES.glob("*.xml"))
        self.assertTrue(files, "no samples found")
        for path in files:
            with self.subTest(path.name):
                result = implode_mod.roundtrip(path, self.tmp / path.stem)
                self.assertEqual(result.problems, [], path.name)
                self.assertEqual(result.warnings, [], path.name)

    def test_procscript_survives_byte_for_byte(self):
        source = SAMPLES / "HILIGHTROW_Include_Proc.xml"
        work = self.tmp / "w"
        tree, out = work / "tree", work / "out.xml"
        explode_mod.explode(source, tree, force=True)
        implode_mod.implode(tree, out, force=True)
        original = source.read_bytes()
        # Compare the actual ProcScript text, not just element structure.
        left = {r[1].get("name"): r[2] for r in implode_mod.flatten(original)
                if r[0].endswith("DAT")}
        right = {r[1].get("name"): r[2] for r in implode_mod.flatten(out.read_bytes())
                 if r[0].endswith("DAT")}
        self.assertIn("UTEXT", left)
        self.assertEqual(left["UTEXT"], right["UTEXT"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
