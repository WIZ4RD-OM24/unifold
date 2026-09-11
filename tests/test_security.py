"""Security properties, pinned so they cannot regress quietly.

Uniface exports arrive from elsewhere: a colleague, a shared drive, a git
repository, a vendor. They are untrusted input, and so are exploded trees. The
protections here are mostly structural rather than added defences -- the point
of these tests is that a later refactor cannot remove them by accident.
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from unifold import explode as explode_mod  # noqa: E402
from unifold import probe as probe_mod  # noqa: E402

CANARY = "SECRET-CANARY-DO-NOT-LEAK"


class SecurityCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="unifold-sec-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.secret = self.tmp / "secret.txt"
        self.secret.write_text(CANARY, encoding="utf-8")

    def write(self, name: str, text: str) -> Path:
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return path


class TestXmlEntityHandling(SecurityCase):
    """`prepare()` replaces the document's DOCTYPE with a flat internal subset.

    That is what neutralises both classic XML entity attacks: an external
    entity is never declared as SYSTEM, and a nested one loses its nesting.
    """

    def test_external_entity_does_not_read_a_local_file(self):
        source = self.write("xxe.xml", """<?xml version="1.0"?>
<!DOCTYPE UNIFACE [ <!ENTITY xxe SYSTEM "file:///%s"> ]>
<UNIFACE release="9.7"><TABLE><DSC name="T"><FLD name="A"/></DSC>
<OCC><DAT name="A">&xxe;</DAT></OCC></TABLE></UNIFACE>
""" % self.secret.as_posix())
        out = self.tmp / "out"
        explode_mod.explode(source, out)
        for path in out.rglob("*"):
            if path.is_file():
                text = path.read_text(encoding="utf-8", errors="replace")
                self.assertNotIn(CANARY, text, path.name)

    def test_external_entity_becomes_an_inert_placeholder(self):
        source = self.write("xxe2.xml", """<?xml version="1.0"?>
<!DOCTYPE UNIFACE [ <!ENTITY xxe SYSTEM "file:///etc/passwd"> ]>
<UNIFACE release="9.7"><TABLE><DSC name="T"><FLD name="A"/></DSC>
<OCC><DAT name="A">&xxe;</DAT></OCC></TABLE></UNIFACE>
""")
        out = self.tmp / "out"
        explode_mod.explode(source, out)
        text = (out / "T/occ_001/properties.txt").read_text(encoding="utf-8")
        self.assertIn(probe_mod.placeholder_for("xxe"), text)
        self.assertNotIn("root:", text)

    def test_nested_entity_expansion_stays_small(self):
        # A billion-laughs payload: each entity ten copies of the previous one.
        declarations = "".join(
            '<!ENTITY e%d "%s">' % (i, ("&e%d;" % (i - 1)) * 10)
            for i in range(1, 12)
        )
        source = self.write("bomb.xml", """<?xml version="1.0"?>
<!DOCTYPE UNIFACE [ <!ENTITY e0 "AAAAAAAAAA">%s ]>
<UNIFACE release="9.7"><TABLE><DSC name="T"><FLD name="A"/></DSC>
<OCC><DAT name="A">&e11;</DAT></OCC></TABLE></UNIFACE>
""" % declarations)
        self.assertLess(source.stat().st_size, 4000)
        out = self.tmp / "out"
        explode_mod.explode(source, out)
        written = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
        self.assertLess(written, 200_000, "entity expansion was not contained")

    def test_probe_reports_substituted_entities_rather_than_hiding_them(self):
        source = self.write("ents.xml", """<?xml version="1.0"?>
<UNIFACE release="9.7"><TABLE><DSC name="T"><FLD name="A"/></DSC>
<OCC><DAT name="A">&uSEP;</DAT></OCC></TABLE></UNIFACE>
""")
        result = probe_mod.probe(source)
        self.assertTrue(any("UNIFACE.DTD" in note for note in result.notes))


class TestExplodePathSafety(SecurityCase):
    def test_traversal_in_object_names_cannot_escape_the_output(self):
        source = self.write("trav.xml", """<?xml version="1.0"?>
<UNIFACE release="9.7"><TABLE>
<DSC name="../../../evil"><FLD name="ULABEL"/></DSC>
<OCC><DAT name="ULABEL">../../../../pwned</DAT></OCC>
<OCC><DAT name="ULABEL">C:\\Windows\\System32\\evil</DAT></OCC>
<OCC><DAT name="ULABEL">..</DAT></OCC>
</TABLE></UNIFACE>
""")
        out = self.tmp / "out"
        explode_mod.explode(source, out)
        out_resolved = out.resolve()
        for path in out.rglob("*"):
            with self.subTest(path.name):
                path.resolve().relative_to(out_resolved)   # raises if escaped

    def test_sanitised_names_are_still_distinct(self):
        self.assertNotEqual(explode_mod.safe_name("a/b"),
                            explode_mod.safe_name("a"))

    def test_parent_reference_becomes_a_safe_name(self):
        self.assertEqual(explode_mod.safe_name(".."), "unnamed")
        self.assertEqual(explode_mod.safe_name("../.."), "unnamed")

    def test_absolute_windows_path_is_flattened(self):
        flattened = explode_mod.safe_name("C:\\Windows\\System32")
        self.assertNotIn("\\", flattened)
        self.assertNotIn(":", flattened)


class TestNoNetworkOrExecution(unittest.TestCase):
    """The runtime must not reach the network or run anything."""

    def test_runtime_modules_import_no_network_or_process_libraries(self):
        forbidden = ("subprocess", "socket", "urllib.request", "requests",
                     "http.client", "os.system", "eval(", "exec(")
        for module in sorted((ROOT / "src" / "unifold").glob("*.py")):
            text = module.read_text(encoding="utf-8")
            for name in forbidden:
                with self.subTest(module=module.name, forbidden=name):
                    self.assertNotIn(name, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
