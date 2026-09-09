"""Tests for the exploder.

Synthetic cases cover the mechanics and edge cases; the real-export cases assert
against genuine Uniface files and skip when samples are absent.
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from unifold import explode as explode_mod  # noqa: E402

SAMPLES = ROOT / "samples" / "learn-palettes"
V97 = SAMPLES / "HILIGHTROW_Include_Proc.xml"
V102 = SAMPLES / "cpt_showemployees.xml"
SKIP = not SAMPLES.is_dir()
REASON = "real samples absent -- run scripts/fetch_samples.py"

MINIMAL = """<?xml version='1.0' encoding='UTF-8'?>
<UNIFACE release="9.7" xmlengine="2.0">
<TABLE>
  <DSC name="USOURCE"><FLD name="ULABEL"/><FLD name="UTEXT"/></DSC>
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


class ExplodeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="unifold-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def write_source(self, text: str) -> Path:
        path = self.tmp / "src.xml"
        path.write_text(text, encoding="utf-8")
        return path


class TestSafeName(unittest.TestCase):
    def test_strips_path_hostile_characters(self):
        self.assertEqual(explode_mod.safe_name("A/B\\C"), "A_B_C")
        self.assertEqual(explode_mod.safe_name("  spaced name "), "spaced_name")

    def test_empty_falls_back(self):
        self.assertEqual(explode_mod.safe_name(""), "unnamed")
        self.assertEqual(explode_mod.safe_name("///"), "unnamed")

    def test_windows_reserved_names_are_escaped(self):
        # A column or label called CON would be unwritable on Windows.
        self.assertEqual(explode_mod.safe_name("CON"), "CON_")
        self.assertEqual(explode_mod.safe_name("aux"), "aux_")

    def test_length_is_bounded(self):
        self.assertLessEqual(len(explode_mod.safe_name("x" * 500)), 80)


class TestSplitRules(unittest.TestCase):
    def test_multiline_becomes_a_block_scalar_stays_inline(self):
        occ = explode_mod.Occurrence("T", "K", {
            "SHORT": "abc",
            "MULTI": "line one\nline two",
            "LONG": "y" * (explode_mod.INLINE_MAX + 1),
            "EMPTY": "   ",
            "NONE": None,
        })
        properties, blocks = occ.split()
        self.assertEqual(properties, {"SHORT": "abc"})
        self.assertEqual(sorted(blocks), ["LONG", "MULTI"])

    def test_procscript_gets_proc_extension(self):
        self.assertEqual(explode_mod.extension_for("entry foo\nend"), ".proc")
        self.assertEqual(explode_mod.extension_for("just some prose\nhere"), ".txt")


class TestExplodeMechanics(ExplodeCase):
    def test_writes_expected_tree(self):
        source = self.write_source(MINIMAL)
        out = self.tmp / "out"
        result = explode_mod.explode(source, out)
        self.assertEqual(result.release, "9.7")
        self.assertIn("USOURCE/MYPROC/properties.txt", result.files)
        self.assertIn("USOURCE/MYPROC/UTEXT.proc", result.files)
        self.assertEqual(
            (out / "USOURCE/MYPROC/UTEXT.proc").read_text(encoding="utf-8"),
            "entry foo\n  return 0\nend\n",
        )
        self.assertIn("ULABEL: MYPROC",
                      (out / "USOURCE/MYPROC/properties.txt").read_text(encoding="utf-8"))

    def test_empty_columns_are_skipped_and_counted(self):
        result = explode_mod.explode(self.write_source(MINIMAL), self.tmp / "out")
        self.assertEqual(result.skipped_empty, 1)
        self.assertNotIn("USOURCE/MYPROC/UEMPTY.txt", result.files)

    def test_is_deterministic(self):
        source = self.write_source(MINIMAL)
        a, b = self.tmp / "a", self.tmp / "b"
        ra = explode_mod.explode(source, a)
        rb = explode_mod.explode(source, b)
        self.assertEqual(ra.files, rb.files)
        for rel in ra.files:
            self.assertEqual((a / rel).read_bytes(), (b / rel).read_bytes(), rel)

    def test_refuses_to_write_into_a_non_empty_directory(self):
        source = self.write_source(MINIMAL)
        out = self.tmp / "out"
        out.mkdir()
        (out / "existing.txt").write_text("do not clobber me", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            explode_mod.explode(source, out)
        self.assertEqual((out / "existing.txt").read_text(encoding="utf-8"),
                         "do not clobber me")
        # ...unless explicitly forced.
        explode_mod.explode(source, out, force=True)
        self.assertTrue((out / "manifest.txt").is_file())

    def test_dry_run_writes_nothing_but_still_reports(self):
        out = self.tmp / "out"
        result = explode_mod.explode(self.write_source(MINIMAL), out, dry_run=True)
        self.assertTrue(result.files)
        self.assertFalse(out.exists())

    def test_duplicate_keys_do_not_overwrite_each_other(self):
        duplicated = MINIMAL.replace(
            "</TABLE>",
            "<OCC><DAT name=\"ULABEL\">MYPROC</DAT>"
            "<DAT name=\"UTEXT\">second body here</DAT></OCC></TABLE>",
        )
        result = explode_mod.explode(self.write_source(duplicated), self.tmp / "out")
        directories = {rel.split("/")[1] for rel in result.files if "/" in rel}
        self.assertEqual(directories, {"MYPROC", "MYPROC__2"})

    def test_row_without_a_label_gets_a_positional_key(self):
        anonymous = MINIMAL.replace('<DAT name="ULABEL">MYPROC</DAT>', "")
        result = explode_mod.explode(self.write_source(anonymous), self.tmp / "out")
        self.assertTrue(any("occ_001" in rel for rel in result.files))


@unittest.skipIf(SKIP, REASON)
class TestExplodeRealExports(ExplodeCase):
    def test_97_include_proc_yields_readable_procscript(self):
        out = self.tmp / "out97"
        result = explode_mod.explode(V97, out)
        self.assertEqual(result.release, "9.7")
        body = (out / "USOURCE/HILIGHTROW/UTEXT.proc").read_text(encoding="utf-8")
        self.assertIn("entry OccurrenceSetFieldColors", body)
        self.assertIn("endparams", body)

    def test_102_component_separates_triggers_fields_and_entities(self):
        out = self.tmp / "out102"
        result = explode_mod.explode(V102, out)
        self.assertEqual(result.release, "10.2")
        self.assertIn("UFORM/SHOWEMPLOYEES/USCRIPT.proc", result.files)
        self.assertIn("UXGROUP/EMPLOYEE/properties.txt", result.files)
        self.assertIn("UXFIELD/NAME/properties.txt", result.files)
        script = (out / "UFORM/SHOWEMPLOYEES/USCRIPT.proc").read_text(encoding="utf-8")
        self.assertIn("operation exec", script)

    def test_manifest_records_provenance_and_entity_substitution(self):
        out = self.tmp / "outman"
        explode_mod.explode(V102, out)
        manifest = (out / "manifest.txt").read_text(encoding="utf-8")
        self.assertIn("release       10.2", manifest)
        self.assertIn("UNIFACE.DTD", manifest)
        self.assertIn("&uSEP;", manifest)

    def test_both_versions_explode_through_the_same_code_path(self):
        # The point of the format being self-describing: no version branching.
        for source in (V97, V102):
            with self.subTest(source.name):
                result = explode_mod.explode(source, self.tmp / source.stem)
                self.assertTrue(result.files)
                self.assertGreater(result.occurrence_count, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
