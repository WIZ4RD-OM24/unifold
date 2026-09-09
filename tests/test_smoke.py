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

FIXTURES = ROOT / "tests" / "fixtures"
A = FIXTURES / "synthetic_component.xml"
B = FIXTURES / "synthetic_component_reexported.xml"


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
