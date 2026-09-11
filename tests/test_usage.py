"""Tests for entity and field usage tracking.

This is impact analysis: change a field, see which components break. Getting it
wrong in the reassuring direction -- reporting fewer users than really exist --
would be worse than not having the feature, so the tests lean on that case.
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from unifold import usage  # noqa: E402

SAMPLES = ROOT / "samples" / "learn-palettes"
SKIP = not SAMPLES.is_dir()
REASON = "real samples absent -- run scripts/fetch_samples.py"


class WorkspaceCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="unifold-usage-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def properties(self, relative: str, **values) -> None:
        path = self.tmp / relative / "properties.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join("%s: %s\n" % (k, v) for k, v in sorted(values.items())),
            encoding="utf-8",
        )

    def sample_workspace(self):
        # Model: BOOTSTRAP.EMPLOYEE with fields NAME and AGE.
        self.properties("m/UCGROUP/EMPLOYEE", U_VLAB="BOOTSTRAP", U_TLAB="EMPLOYEE",
                        U_GLAB="EMPLOYEE")
        self.properties("m/UCFIELD/NAME", U_VLAB="BOOTSTRAP", U_TLAB="EMPLOYEE",
                        U_FLAB="NAME", U_DTYP="S")
        self.properties("m/UCFIELD/AGE", U_VLAB="BOOTSTRAP", U_TLAB="EMPLOYEE",
                        U_FLAB="AGE", U_DTYP="N")
        self.properties("m/UCFIELD/UNUSED", U_VLAB="BOOTSTRAP", U_TLAB="EMPLOYEE",
                        U_FLAB="UNUSED", U_DTYP="S")
        # Two components using the entity; only one uses AGE.
        for component in ("COMPA", "COMPB"):
            self.properties("%s/UXGROUP/EMPLOYEE" % component, UFORM=component,
                            ULABEL="EMPLOYEE", UBASE="BOOTSTRAP", U_GLAB="EMPLOYEE")
            self.properties("%s/UXFIELD/NAME" % component, UFORM=component,
                            ULABEL="NAME", GRP="EMPLOYEE", U_TLAB="EMPLOYEE",
                            UBASE="BOOTSTRAP")
        self.properties("COMPA/UXFIELD/AGE", UFORM="COMPA", ULABEL="AGE",
                        GRP="EMPLOYEE", U_TLAB="EMPLOYEE", UBASE="BOOTSTRAP")
        return usage.build(self.tmp)


class TestIndexing(WorkspaceCase):
    def test_definitions_are_qualified_by_model(self):
        index = self.sample_workspace()
        keys = sorted(d.key for d in index.definitions)
        self.assertIn("BOOTSTRAP.EMPLOYEE", keys)
        self.assertIn("BOOTSTRAP.EMPLOYEE.NAME", keys)

    def test_field_type_is_captured(self):
        index = self.sample_workspace()
        age = next(d for d in index.definitions if d.key.endswith(".AGE"))
        self.assertEqual(age.dtype, "N")

    def test_unrelated_tables_are_ignored(self):
        self.properties("x/UFORM/THING", ULABEL="THING")
        index = usage.build(self.tmp)
        self.assertEqual(index.definitions, [])
        self.assertEqual(index.usages, [])

    def test_same_model_entity_in_two_models_stays_distinct(self):
        self.properties("m1/UCGROUP/E", U_VLAB="MODELA", U_TLAB="E", U_GLAB="E")
        self.properties("m2/UCGROUP/E", U_VLAB="MODELB", U_TLAB="E", U_GLAB="E")
        index = usage.build(self.tmp)
        self.assertEqual(sorted(d.key for d in index.definitions),
                         ["MODELA.E", "MODELB.E"])


class TestQueries(WorkspaceCase):
    def test_entity_lookup_finds_every_component(self):
        index = self.sample_workspace()
        _, usages = usage.match(index, "EMPLOYEE", "entity")
        self.assertEqual(sorted({u.component for u in usages}), ["COMPA", "COMPB"])

    def test_field_lookup_is_narrower_than_its_entity(self):
        index = self.sample_workspace()
        _, age = usage.match(index, "AGE", "field")
        self.assertEqual([u.component for u in age], ["COMPA"])

    def test_lookup_accepts_a_qualified_name(self):
        index = self.sample_workspace()
        definitions, _ = usage.match(index, "BOOTSTRAP.EMPLOYEE.NAME", "field")
        self.assertEqual(len(definitions), 1)

    def test_lookup_is_case_insensitive(self):
        index = self.sample_workspace()
        _, usages = usage.match(index, "employee", "entity")
        self.assertTrue(usages)

    def test_entity_and_field_namespaces_do_not_collide(self):
        # Asking for an entity must not return a field of the same name.
        index = self.sample_workspace()
        definitions, _ = usage.match(index, "NAME", "entity")
        self.assertEqual(definitions, [])

    def test_unused_definition_is_reported(self):
        index = self.sample_workspace()
        self.assertIn("BOOTSTRAP.EMPLOYEE.UNUSED",
                      [d.key for d in index.unused_definitions()])

    def test_usage_without_a_definition_is_reported(self):
        self.properties("c/UXGROUP/GHOST", UFORM="C", ULABEL="GHOST",
                        UBASE="NOMODEL", U_GLAB="GHOST")
        index = usage.build(self.tmp)
        self.assertEqual([u.key for u in index.undefined_usages()],
                         ["NOMODEL.GHOST"])


class TestRendering(WorkspaceCase):
    def test_component_view_does_not_repeat_fields(self):
        # The same component can appear in several exports; listing each field
        # once per export would be noise.
        index = self.sample_workspace()
        self.properties("dup/UXFIELD/NAME", UFORM="COMPA", ULABEL="NAME",
                        GRP="EMPLOYEE", U_TLAB="EMPLOYEE", UBASE="BOOTSTRAP")
        index = usage.build(self.tmp)
        text = usage.render_component(index, "COMPA")
        self.assertEqual(text.count("NAME"), 1)

    def test_summary_lists_shared_entities_with_their_blast_radius(self):
        text = usage.render(self.sample_workspace())
        self.assertIn("BOOTSTRAP.EMPLOYEE", text)
        self.assertIn("COMPA, COMPB", text)

    def test_summary_pluralises_correctly(self):
        text = usage.render(self.sample_workspace())
        self.assertIn("1 entity", text)
        self.assertIn("3 fields", text)

    def test_unknown_name_is_reported_kindly(self):
        text = usage.render_lookup(self.sample_workspace(), "nope", "entity")
        self.assertIn("Not found", text)

    def test_json_is_serialisable(self):
        import json
        payload = json.loads(usage.to_json(self.sample_workspace()))
        self.assertIn("BOOTSTRAP.EMPLOYEE.UNUSED", payload["unused"])


@unittest.skipIf(SKIP, REASON)
class TestAgainstRealExports(WorkspaceCase):
    def build_real(self):
        from unifold import explode as explode_mod

        for source in sorted(SAMPLES.glob("*.xml")):
            explode_mod.explode(source, self.tmp / source.stem, force=True)
        return usage.build(self.tmp)

    def test_entity_usage_spans_multiple_components(self):
        index = self.build_real()
        _, usages = usage.match(index, "EMPLOYEE", "entity")
        components = {u.component for u in usages}
        self.assertGreater(len(components), 1)
        self.assertIn("SHOWEMPLOYEES", components)

    def test_field_is_traced_to_its_model_definition(self):
        index = self.build_real()
        definitions, usages = usage.match(index, "AGE", "field")
        self.assertTrue(definitions, "field definition not found in the model")
        self.assertEqual(definitions[0].entity, "EMPLOYEE")
        self.assertTrue(usages)


if __name__ == "__main__":
    unittest.main(verbosity=2)
