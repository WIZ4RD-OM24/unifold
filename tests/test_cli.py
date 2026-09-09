"""Tests for command-line behaviour.

Users hit malformed input more often than anything else: a truncated export, a
compiled object mistaken for an export, a hand-edited sidecar. A stack trace
tells them nothing they can act on, so these tests pin the failure messages and
exit codes rather than only the happy path.
"""

import contextlib
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from unifold import cli  # noqa: E402
from unifold import explode as explode_mod  # noqa: E402

SAMPLES = ROOT / "samples" / "learn-palettes"
COMPONENT = SAMPLES / "cpt_showemployees.xml"
SKIP = not COMPONENT.is_file()
REASON = "real samples absent -- run scripts/fetch_samples.py"


def run(*argv):
    """Invoke the CLI, returning (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class CliCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="unifold-cli-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def write(self, name: str, content) -> Path:
        path = self.tmp / name
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path


class TestMissingInput(CliCase):
    def test_missing_file_is_reported(self):
        code, _, err = run("probe", str(self.tmp / "nope.xml"))
        self.assertEqual(code, 2)
        self.assertIn("no such file", err)

    def test_missing_directory_is_reported(self):
        code, _, err = run("xref", str(self.tmp / "nope"))
        self.assertEqual(code, 2)
        self.assertIn("no such directory", err)


class TestMalformedInput(CliCase):
    def test_non_xml_gets_a_message_not_a_traceback(self):
        junk = self.write("junk.xml", "this is not xml")
        code, _, err = run("explode", str(junk), str(self.tmp / "out"))
        self.assertEqual(code, 2)
        self.assertIn("not valid XML", err)
        self.assertNotIn("Traceback", err)

    def test_truncated_export_suggests_re_exporting(self):
        truncated = self.write(
            "cut.xml",
            "<?xml version='1.0'?>\n<UNIFACE release=\"9.7\">\n<TABLE>\n<DSC name=",
        )
        code, _, err = run("explode", str(truncated), str(self.tmp / "out"))
        self.assertEqual(code, 2)
        self.assertIn("truncated", err)
        self.assertIn("probe", err)

    def test_binary_input_is_explained(self):
        # A compiled .frm is not an export; saying so beats a decode traceback.
        binary = self.write("compiled.frm", b"\x00\x01\x02\xff\xfe\x03binary")
        code, _, err = run("explode", str(binary), str(self.tmp / "out"))
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", err)

    def test_no_partial_output_is_left_behind(self):
        junk = self.write("junk.xml", "not xml")
        out = self.tmp / "out"
        run("explode", str(junk), str(out))
        self.assertFalse(out.exists(), "a failed explode must not leave a tree")


@unittest.skipIf(SKIP, REASON)
class TestCorruptSidecar(CliCase):
    def test_corrupt_sidecar_names_the_file_and_the_fix(self):
        tree = self.tmp / "tree"
        explode_mod.explode(COMPONENT, tree)
        (tree / explode_mod.SIDECAR).write_text("not json", encoding="utf-8")
        code, _, err = run("implode", str(tree), str(self.tmp / "out.xml"))
        self.assertEqual(code, 2)
        self.assertIn(explode_mod.SIDECAR, err)
        self.assertIn("explode", err)
        self.assertNotIn("Traceback", err)

    def test_failed_implode_writes_no_output_file(self):
        tree = self.tmp / "tree"
        explode_mod.explode(COMPONENT, tree)
        (tree / explode_mod.SIDECAR).write_text("not json", encoding="utf-8")
        target = self.tmp / "out.xml"
        run("implode", str(tree), str(target))
        self.assertFalse(target.exists())


@unittest.skipIf(SKIP, REASON)
class TestHappyPath(CliCase):
    def test_explode_then_implode_then_roundtrip(self):
        tree = self.tmp / "tree"
        self.assertEqual(run("explode", str(COMPONENT), str(tree))[0], 0)
        self.assertEqual(
            run("implode", str(tree), str(self.tmp / "out.xml"))[0], 0
        )
        code, out, _ = run("roundtrip", str(COMPONENT))
        self.assertEqual(code, 0)
        self.assertIn("faithful", out)

    def test_explode_refuses_a_non_empty_directory(self):
        tree = self.tmp / "tree"
        tree.mkdir()
        (tree / "keep.txt").write_text("mine", encoding="utf-8")
        code, _, err = run("explode", str(COMPONENT), str(tree))
        self.assertEqual(code, 2)
        self.assertIn("--force", err)
        self.assertEqual((tree / "keep.txt").read_text(encoding="utf-8"), "mine")


if __name__ == "__main__":
    unittest.main(verbosity=2)
