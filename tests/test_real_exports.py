"""Regression tests against real Uniface exports.

Unlike the synthetic fixtures, these assert facts measured from genuine files
published by Rocket in `uniface/learn-palettes`. Run scripts/fetch_samples.py
first; every test skips cleanly if the samples are absent, so a fresh clone
still passes.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from unifold import probe as probe_mod  # noqa: E402
from unifold import schemadiff as schemadiff_mod  # noqa: E402

SAMPLES = ROOT / "samples" / "learn-palettes"
V97 = SAMPLES / "HILIGHTROW_Include_Proc.xml"
V102 = SAMPLES / "cpt_showemployees.xml"
PROJECT = SAMPLES / "prj_full_demoproject.xml"

SKIP = not SAMPLES.is_dir()
REASON = "real samples absent -- run scripts/fetch_samples.py"


@unittest.skipIf(SKIP, REASON)
class TestRealExportShape(unittest.TestCase):
    """The container format, measured rather than assumed."""

    def test_every_sample_parses_without_error(self):
        files = sorted(SAMPLES.glob("*.xml"))
        self.assertTrue(files, "no samples found")
        for path in files:
            with self.subTest(path.name):
                result = probe_mod.probe(path)
                self.assertEqual(result.root, "UNIFACE")
                errors = [n for n in result.notes if "parse error" in n]
                self.assertEqual(errors, [], "%s: %s" % (path.name, errors))

    def test_container_format_is_uniform_across_versions_and_object_types(self):
        # An include proc, a component, a model and a project all serialise
        # through the same six paths. This is why one parser covers both
        # versions: only the repository tables inside differ.
        expected = {
            "UNIFACE",
            "UNIFACE/TABLE",
            "UNIFACE/TABLE/DSC",
            "UNIFACE/TABLE/DSC/FLD",
            "UNIFACE/TABLE/OCC",
            "UNIFACE/TABLE/OCC/DAT",
        }
        for path in sorted(SAMPLES.glob("*.xml")):
            with self.subTest(path.name):
                self.assertEqual(set(probe_mod.probe(path).nodes), expected)

    def test_exports_are_bom_prefixed(self):
        # Real exports carry a UTF-8 BOM. The "is this XML" guard must see past
        # it -- this is the bug the first real file exposed.
        result = probe_mod.probe(V97)
        self.assertEqual(result.detected_bom, "utf-8-sig")
        self.assertEqual(result.declared_encoding, "UTF-8")
        self.assertIsNotNone(result.root)

    def test_release_attribute_identifies_the_version(self):
        self.assertIn("9.7", probe_mod.probe(V97).nodes["UNIFACE"].attrs["release"].distinct)
        self.assertIn("10.2", probe_mod.probe(V102).nodes["UNIFACE"].attrs["release"].distinct)


@unittest.skipIf(SKIP, REASON)
class TestCustomEntities(unittest.TestCase):
    """Exports reference UNIFACE.DTD, which is not shipped with them."""

    def test_undefined_entities_are_declared_not_dropped(self):
        raw = V102.read_bytes()
        self.assertIn(b"&uFRM;", raw)
        prepared, names = probe_mod.prepare(raw)
        self.assertIn("uFRM", names)
        self.assertIn("uSEP", names)
        # Standard XML entities must not be redeclared.
        self.assertNotIn("lt", names)
        self.assertNotIn("gt", names)
        self.assertIn(b"<!ENTITY uFRM", prepared)

    def test_placeholder_survives_into_parsed_content(self):
        result = probe_mod.probe(V102)
        samples = " ".join(result.nodes["UNIFACE/TABLE/OCC/DAT"].samples)
        self.assertIn(probe_mod.placeholder_for("uSEP"), samples)

    def test_entity_substitution_is_reported_to_the_user(self):
        notes = probe_mod.probe(V102).notes
        self.assertTrue(any("UNIFACE.DTD" in n for n in notes))

    def test_file_without_custom_entities_needs_no_declarations(self):
        prepared, names = probe_mod.prepare(b"<?xml version='1.0'?><A>x &lt; y</A>")
        self.assertEqual(names, [])
        self.assertNotIn(b"<!ENTITY", prepared)


@unittest.skipIf(SKIP, REASON)
class TestProcScriptStorage(unittest.TestCase):
    def test_procscript_is_stored_as_plain_text_not_encoded(self):
        # The single most important finding for the exploder: ProcScript is
        # verbatim text in DAT elements, not base64 or compressed.
        node = probe_mod.probe(V97).nodes["UNIFACE/TABLE/OCC/DAT"]
        self.assertIn("procscript?", node.text_tags)
        self.assertNotIn("base64", node.text_tags)
        joined = " ".join(node.samples)
        self.assertIn("entry", joined)


@unittest.skipIf(SKIP, REASON)
class TestVersionCompatibility(unittest.TestCase):
    def test_97_and_102_share_the_entire_element_vocabulary(self):
        d = schemadiff_mod.diff(
            probe_mod.probe(V97), probe_mod.probe(V102), "9.7", "10.2"
        )
        self.assertEqual(d.only_left, [])
        self.assertEqual(d.only_right, [])
        self.assertEqual(d.vocab_overlap, 1.0)

    def test_only_root_attribute_differs_between_versions(self):
        d = schemadiff_mod.diff(probe_mod.probe(V97), probe_mod.probe(V102))
        self.assertEqual(list(d.attr_deltas), ["UNIFACE"])
        _, only_102 = d.attr_deltas["UNIFACE"]
        self.assertEqual(only_102, ["repversion"])


@unittest.skipIf(SKIP, REASON)
class TestLargeExport(unittest.TestCase):
    def test_project_export_parses_and_holds_many_tables(self):
        result = probe_mod.probe(PROJECT)
        self.assertEqual(result.root, "UNIFACE")
        self.assertGreater(result.nodes["UNIFACE/TABLE"].count, 1)
        self.assertGreater(result.nodes["UNIFACE/TABLE/OCC/DAT"].count, 100)


if __name__ == "__main__":
    unittest.main(verbosity=2)
