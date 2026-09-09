"""unifold -- turn Uniface exports into something a human (or an AI) can read."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import compare as compare_mod
from . import probe as probe_mod

__version__ = "0.1.0"


def cmd_probe(args) -> int:
    path = Path(args.file)
    if not path.is_file():
        print("no such file: %s" % path, file=sys.stderr)
        return 2
    result = probe_mod.probe(path)
    if args.json:
        print(probe_mod.to_json(result))
    else:
        print(probe_mod.render(result, show_samples=not args.no_samples))
    return 0


def cmd_compare(args) -> int:
    left, right = Path(args.left), Path(args.right)
    for p in (left, right):
        if not p.is_file():
            print("no such file: %s" % p, file=sys.stderr)
            return 2
    drop = set(args.ignore_attr or [])
    result = compare_mod.compare(left, right, drop=drop)
    print(compare_mod.render(result))
    return 0 if result.sorted_identical else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="unifold",
        description="Read Uniface export files as human-readable source.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser(
        "probe",
        help="report the structure of an export file (no schema assumed)",
        description=(
            "Reads a Uniface export and reports its actual element tree, "
            "attribute cardinalities, and where ProcScript or encoded blobs "
            "live. Run this first on a real export; its output is what the "
            "exploder mapping is written against."
        ),
    )
    p.add_argument("file", help="path to a Uniface export file")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--no-samples", action="store_true", help="omit content samples")
    p.set_defaults(func=cmd_probe)

    c = sub.add_parser(
        "compare",
        help="check whether two exports of the same object are stable",
        description=(
            "Export the same unchanged object twice, then compare. Reports "
            "whether the difference is nil, cosmetic (whitespace/attribute "
            "order), ordering-only, or genuine. Exit code 0 means the exports "
            "agree once ordering is normalised."
        ),
    )
    c.add_argument("left")
    c.add_argument("right")
    c.add_argument(
        "--ignore-attr",
        action="append",
        metavar="NAME",
        help="attribute to exclude (repeatable) -- e.g. an export timestamp",
    )
    c.set_defaults(func=cmd_compare)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
