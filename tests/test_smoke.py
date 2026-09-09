"""Smoke tests against the synthetic fixtures.

These prove the machinery works. They prove nothing about the real Uniface 9.7
schema -- the fixtures are invented. Once a genuine export is available, add it
under tests/fixtures/real/ and assert against that instead.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from unifold import compare as compare_mod  # noqa: E402
from unifold import probe as probe_mod  # noqa: E402
from unifold import schemadiff as schemadiff_mod  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
A = FIXTURES / "synthetic_component.xml"
B = FIXTURES / "synthetic_component_reexported.xml"
DIALECT_B = FIXTURES / "synthetic_dialect_b.xml"


class TestClassify(unittest.TestCase):
    def test_detects_procscript(self):
        tags = probe_mod.classify("trigger exec\n  retrieve/e\nendtrigger")
        self.assertIn("procscript?", tags)
        self.assertIn("multiline", tags)

    def test_detects_base64_only_when_it_decodes(self):
        good = "TGF5b3V0IGJsb2IgcGxhY2Vob2xkZXIgZm9yIHRlc3RpbmcgcHVycG9zZXMgb25seS4="
        self.assertIn("base64", probe_mod.classify(good))
        # Right alphabet, wrong length -- must not be claimed as base64.
        self.assertNotIn("base64", probe_mod.classify("A" * 41))

    def test_empty(self):
        self.assertEqual(probe_mod.classify("   \n  "), ["empty"])


class TestProbe(unittest.TestCase):
    def setUp(self):
        self.result = probe_mod.probe(A)

    def test_finds_root_and_encoding(self):
        self.assertEqual(self.result.root, "UNIFACE")
        self.assertEqual(self.result.declared_encoding, "UTF-8")
        self.assertIsNone(self.result.detected_bom)
        self.assertEqual(self.result.notes, [])

    def test_reports_nesting_and_cardinality(self):
        nodes = self.result.nodes
        self.assertIn("UNIFACE/UFORM/UENTITY/UFIELD", nodes)
        # Two UENTITY siblings under one UFORM.
        self.assertEqual(nodes["UNIFACE/UFORM/UENTITY"].max_siblings, 2)
        # CUSTOMER has three fields, ORDER has two -- max is what matters.
        self.assertEqual(nodes["UNIFACE/UFORM/UENTITY/UFIELD"].count, 5)
        self.assertEqual(nodes["UNIFACE/UFORM/UENTITY/UFIELD"].max_siblings, 3)

    def test_locates_procscript_and_blobs(self):
        nodes = self.result.nodes
        self.assertIn("procscript?", nodes["UNIFACE/UFORM/UPROC"].text_tags)
        self.assertIn("base64", nodes["UNIFACE/UFORM/ULAYOUT"].text_tags)

    def test_attribute_enumeration(self):
        attr = self.result.nodes["UNIFACE/UFORM/UENTITY/UFIELD"].attrs["TYPE"]
        self.assertTrue(attr.looks_enumerated)
        self.assertEqual({v for v, _ in attr.distinct.items()}, {"C", "N"})

    def test_json_is_serialisable(self):
        import json

        json.loads(probe_mod.to_json(self.result))

    def test_non_xml_is_reported_not_crashed(self):
        junk = FIXTURES / "_not_xml.tmp"
        junk.write_bytes(b"UNIFACE TRX 3\x01\x02not xml at all")
        try:
            result = probe_mod.probe(junk)
            self.assertIsNone(result.root)
            self.assertTrue(result.notes)
            self.assertIn("not XML", result.notes[0])
        finally:
            junk.unlink()


class TestCompare(unittest.TestCase):
    def test_shuffled_reexport_is_order_only_once_timestamp_ignored(self):
        result = compare_mod.compare(A, B, drop={"EXPORTED"})
        self.assertFalse(result.byte_identical)
        self.assertFalse(result.canonical_identical)
        self.assertTrue(result.sorted_identical)
        self.assertIn("sibling element ORDER", result.verdict)

    def test_volatile_attribute_surfaces_as_a_real_difference(self):
        result = compare_mod.compare(A, B, drop=set())
        self.assertFalse(result.sorted_identical)
        self.assertTrue(any("EXPORTED" in line for line in result.diff_lines))

    def test_file_compared_against_itself_is_identical(self):
        result = compare_mod.compare(A, A)
        self.assertTrue(result.byte_identical)
        self.assertIn("fully deterministic", result.verdict)


class TestSchemaDiff(unittest.TestCase):
    def setUp(self):
        self.diff = schemadiff_mod.diff(
            probe_mod.probe(A), probe_mod.probe(DIALECT_B), "9.7-ish", "10.4-ish"
        )

    def test_identical_schema_needs_one_mapping(self):
        same = schemadiff_mod.diff(probe_mod.probe(A), probe_mod.probe(A))
        self.assertEqual(same.only_left, [])
        self.assertEqual(same.only_right, [])
        self.assertEqual(same.attr_deltas, {})
        self.assertEqual(same.vocab_overlap, 1.0)
        self.assertIn("one mapping covers both", same.verdict)

    def test_reordered_reexport_has_identical_schema(self):
        # Same dialect, shuffled content -- schema must be identical even though
        # the documents are not.
        same = schemadiff_mod.diff(probe_mod.probe(A), probe_mod.probe(B))
        self.assertEqual(same.only_left, [])
        self.assertEqual(same.only_right, [])

    def test_divergent_dialects_are_reported_as_divergent(self):
        self.assertEqual(self.diff.shared, ["UNIFACE"])
        self.assertIn("UNIFACE/UFORM", self.diff.only_left)
        self.assertIn("UNIFACE/COMPONENT", self.diff.only_right)
        self.assertLess(self.diff.vocab_overlap, 0.3)
        self.assertIn("concept by concept", self.diff.verdict)

    def test_attribute_delta_on_a_shared_path(self):
        # Both roots are UNIFACE with the same attributes, so no delta there.
        self.assertNotIn("UNIFACE", self.diff.attr_deltas)

    def test_render_mentions_both_labels(self):
        text = schemadiff_mod.render(self.diff)
        self.assertIn("9.7-ish", text)
        self.assertIn("10.4-ish", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
