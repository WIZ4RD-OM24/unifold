"""Tests for packed-list decoding.

Decoded views are display-only. The tests that matter most are the ones proving
that: whatever this module renders, `implode` must still reproduce the original
byte for byte, because it reads the raw values and never the decoded file.
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
from unifold import packed  # noqa: E402

SEP, NOT, FRM = packed.SEP, packed.NOT, packed.FRM

SAMPLES = ROOT / "samples" / "learn-palettes"
COMPONENT = SAMPLES / "cpt_showemployees.xml"
SKIP = not COMPONENT.is_file()
REASON = "real samples absent -- run scripts/fetch_samples.py"


class TestDetection(unittest.TestCase):
    def test_recognises_packed_values(self):
        self.assertTrue(packed.is_packed("A%sB" % SEP))
        self.assertTrue(packed.is_packed("%sTYP=F%s" % (FRM, FRM)))

    def test_plain_values_are_not_packed(self):
        self.assertFalse(packed.is_packed("just a string"))
        self.assertFalse(packed.is_packed(""))


class TestSplitting(unittest.TestCase):
    def test_splits_on_outermost_delimiters_only(self):
        value = "A%s%sB%s%sC%sD" % (NOT, SEP, NOT, SEP, SEP)
        self.assertEqual(
            packed.split_top(value),
            ["A%s%sB%s%sC" % (NOT, SEP, NOT, SEP), "D"],
        )

    def test_value_without_delimiters_is_a_single_entry(self):
        self.assertEqual(packed.split_top("alone"), ["alone"])

    def test_unnest_removes_exactly_one_level(self):
        self.assertEqual(packed.unnest("A%s%sB" % (NOT, SEP)), "A%sB" % SEP)
        # Two levels deep becomes one, not zero.
        self.assertEqual(
            packed.unnest("A%s%s%sB" % (NOT, NOT, SEP)), "A%s%sB" % (NOT, SEP)
        )

    def test_trailing_delimiter_yields_a_trailing_empty_entry(self):
        # Uniface lists commonly end with a delimiter; the empty tail is real.
        self.assertEqual(packed.split_top("A%s" % SEP), ["A", ""])


class TestParsing(unittest.TestCase):
    def test_nested_list_is_recovered(self):
        value = "A%s%sB%s%sC%sD" % (NOT, SEP, NOT, SEP, SEP)
        self.assertEqual(packed.parse(value), [["A", "B", "C"], "D"])

    def test_flat_list(self):
        self.assertEqual(packed.parse("A%sB%sC" % (SEP, SEP)), ["A", "B", "C"])

    def test_key_value_entries_are_detected(self):
        entries = packed.parse("MODAL=T%sSPLIT=" % SEP)
        self.assertEqual(packed.as_pairs(entries), [("MODAL", "T"), ("SPLIT", "")])

    def test_non_pair_entries_are_rejected_as_pairs(self):
        self.assertIsNone(packed.as_pairs(["MODAL=T", "not a pair"]))
        self.assertIsNone(packed.as_pairs([["nested"], "A=1"]))


class TestRendering(unittest.TestCase):
    def test_pairs_render_aligned_with_empty_marked(self):
        text = packed.render_entries(packed.parse("MODAL=T%sSPLIT=" % SEP))
        self.assertIn("MODAL = T", text)
        self.assertIn("SPLIT = (empty)", text)

    def test_nested_entries_are_indented(self):
        text = packed.render_entries([["A", "B"], "C"])
        self.assertIn("[1]", text)
        self.assertIn("    [1] A", text)

    def test_formpic_sketch_and_widget_table(self):
        value = ("%sTYP=E%sNAM=FORM%sWID=40%s\n"
                 " %sTYP=F%sNAM=LASTNAME%sWID=28%sHEI=1%s"
                 "%sTYP=F%sNAM=AGE%sWID=9%sHEI=1%s"
                 % (FRM, SEP, SEP, FRM,
                    FRM, SEP, SEP, SEP, FRM,
                    FRM, SEP, SEP, SEP, FRM))
        text = packed.render_formpic(value)
        self.assertIn("[FORM]", text)
        self.assertIn("Widgets", text)
        self.assertIn("TYP", text)
        # Names are padded to their declared width so the sketch keeps the
        # form's proportions. Padding only shows between widgets -- trailing
        # whitespace at end of line is stripped, which is what we want.
        self.assertIn("[LASTNAME]" + " " * (28 - len("[LASTNAME]")) + "[AGE]", text)

    def test_occurrence_with_nothing_packed_renders_nothing(self):
        column = explode_mod.Column
        self.assertEqual(
            packed.render_occurrence([column("A", "plain"), column("B", "also plain")]),
            "",
        )

    def test_occurrence_view_is_labelled_read_only(self):
        column = explode_mod.Column
        text = packed.render_occurrence([column("WINPROP", "MODAL=T%sX=" % SEP)])
        self.assertIn("implode ignores this file", text)
        self.assertIn("WINPROP", text)

    def test_deeply_nested_input_terminates(self):
        # Guard against a pathological value spinning the recursion.
        value = ("A" + NOT * 40 + SEP + "B")
        packed.parse(value)


@unittest.skipIf(SKIP, REASON)
class TestAgainstRealExport(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="unifold-packed-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_decoded_file_is_written_for_a_real_component(self):
        out = self.tmp / "tree"
        result = explode_mod.explode(COMPONENT, out)
        self.assertIn("UFORM/SHOWEMPLOYEES/decoded.txt", result.files)
        text = (out / "UFORM/SHOWEMPLOYEES/decoded.txt").read_text(encoding="utf-8")
        self.assertIn("FORMPIC", text)
        self.assertIn("[LASTNAME]", text)
        self.assertIn("MODAL      = T", text)

    def test_decoding_does_not_affect_fidelity(self):
        result = implode_mod.roundtrip(COMPONENT, self.tmp / "work")
        self.assertEqual(result.problems, [])

    def test_decoded_file_is_ignored_by_implode(self):
        # Corrupt the decoded view entirely; the rebuilt export must not change.
        tree, a, b = self.tmp / "t", self.tmp / "a.xml", self.tmp / "b.xml"
        explode_mod.explode(COMPONENT, tree)
        implode_mod.implode(tree, a)
        (tree / "UFORM/SHOWEMPLOYEES/decoded.txt").write_text(
            "total nonsense", encoding="utf-8"
        )
        implode_mod.implode(tree, b, force=True)
        self.assertEqual(a.read_bytes(), b.read_bytes())


if __name__ == "__main__":
    unittest.main(verbosity=2)
