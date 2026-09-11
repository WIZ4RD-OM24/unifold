"""Guard the declared Python floor.

`pyproject.toml` says `requires-python = ">=3.9"`. That claim was wrong once
already: `Path.write_text(newline=...)` and `read_text(newline=...)` only exist
from 3.10, and 80 tests failed on 3.9 while passing locally on 3.13. CI caught
it, which is what CI is for -- but a test that fails on any interpreter catches
it sooner and explains itself better.
"""

import ast
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

SOURCE_DIRS = ("src", "tests", "scripts")
SKIP_PARTS = {"build", "dist", ".venv", "venv", "__pycache__"}


def declared_floor():
    """Read requires-python out of pyproject, so the two cannot disagree."""
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'requires-python\s*=\s*"[>=~^]*\s*(\d+)\.(\d+)"', text)
    if not match:
        raise AssertionError("could not find requires-python in pyproject.toml")
    return int(match.group(1)), int(match.group(2))


def python_files():
    for directory in SOURCE_DIRS:
        for path in sorted((ROOT / directory).rglob("*.py")):
            if SKIP_PARTS.isdisjoint(path.parts):
                yield path


class TestSyntaxFloor(unittest.TestCase):
    def test_every_file_parses_at_the_declared_floor(self):
        floor = declared_floor()
        self.assertTrue(list(python_files()), "no source files found")
        for path in python_files():
            with self.subTest(path.relative_to(ROOT).as_posix()):
                try:
                    ast.parse(path.read_text(encoding="utf-8"),
                              filename=str(path), feature_version=floor)
                except SyntaxError as exc:
                    self.fail("needs newer than Python %d.%d: %s (line %s)"
                              % (floor[0], floor[1], exc.msg, exc.lineno))


class TestVersionGatedApis(unittest.TestCase):
    """Syntax checking cannot see stdlib arguments that did not exist yet."""

    def test_no_newline_argument_to_path_read_or_write_text(self):
        # Added in 3.10. unifold.explode.write_text / read_text wrap Path.open,
        # which has always accepted it -- use those instead.
        pattern = re.compile(
            r"\.(?:read_text|write_text)\([^)]*newline\s*=", re.S
        )
        for path in python_files():
            if path.name == Path(__file__).name:
                continue        # this file documents the pattern it forbids
            with self.subTest(path.relative_to(ROOT).as_posix()):
                hits = pattern.findall(path.read_text(encoding="utf-8"))
                self.assertEqual(
                    hits, [],
                    "Path.read_text/write_text gained `newline` in 3.10; "
                    "use unifold.explode.read_text/write_text instead",
                )

    def test_the_helpers_preserve_newlines(self):
        # The reason the argument matters at all: without it Windows would
        # rewrite every "\n" as "\r\n" and implode would read back different
        # bytes than explode wrote.
        import tempfile

        from unifold import explode as explode_mod

        with tempfile.TemporaryDirectory() as work:
            target = Path(work) / "sample.txt"
            explode_mod.write_text(target, "a\nb\n")
            self.assertEqual(target.read_bytes(), b"a\nb\n")
            self.assertEqual(explode_mod.read_text(target), "a\nb\n")


if __name__ == "__main__":
    unittest.main(verbosity=2)
