"""unifold -- turn Uniface exports into something a human (or an AI) can read."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import compare as compare_mod
from . import explode as explode_mod
from . import probe as probe_mod
from . import schemadiff as schemadiff_mod

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


def cmd_schemadiff(args) -> int:
    left, right = Path(args.left), Path(args.right)
    for p in (left, right):
        if not p.is_file():
            print("no such file: %s" % p, file=sys.stderr)
            return 2
    lres = probe_mod.probe(left)
    rres = probe_mod.probe(right)
    result = schemadiff_mod.diff(
        lres, rres,
        left_label=args.left_label or left.name,
        right_label=args.right_label or right.name,
    )
    print(schemadiff_mod.render(result))
    return 0


def cmd_explode(args) -> int:
    path = Path(args.file)
    if not path.is_file():
        print("no such file: %s" % path, file=sys.stderr)
        return 2
    try:
        result = explode_mod.explode(
            path, Path(args.out), force=args.force, dry_run=args.dry_run
        )
    except FileExistsError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(explode_mod.render(result, dry_run=args.dry_run))
    return 0


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

    s = sub.add_parser(
        "schemadiff",
        help="diff the schemas of two exports (e.g. 9.7 against 10.4)",
        description=(
            "Probes two exports and reports how their schemas differ: paths "
            "unique to each, attribute differences on shared paths, and how "
            "much element vocabulary they have in common. This is the evidence "
            "the per-version mappings are written against."
        ),
    )
    s.add_argument("left")
    s.add_argument("right")
    s.add_argument("--left-label", metavar="NAME", help='e.g. "9.7"')
    s.add_argument("--right-label", metavar="NAME", help='e.g. "10.4"')
    s.set_defaults(func=cmd_schemadiff)

    e = sub.add_parser(
        "explode",
        help="write an export out as a readable, diffable source tree",
        description=(
            "Writes one directory per repository occurrence: scalar columns "
            "into properties.txt, and every multi-line or long column -- the "
            "ProcScript -- into its own file. Read-only with respect to your "
            "Uniface repository; it only ever writes to the output directory."
        ),
    )
    e.add_argument("file", help="path to a Uniface export file")
    e.add_argument("out", help="output directory")
    e.add_argument("--force", action="store_true",
                   help="write into a non-empty output directory")
    e.add_argument("--dry-run", action="store_true",
                   help="list the files that would be written, write nothing")
    e.set_defaults(func=cmd_explode)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
