"""unifold -- turn Uniface exports into something a human (or an AI) can read."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET

from . import compare as compare_mod
from . import explode as explode_mod
from . import implode as implode_mod
from . import mcp as mcp_mod
from . import probe as probe_mod
from . import schemadiff as schemadiff_mod
from . import usage as usage_mod
from . import workspace as workspace_mod
from . import xref as xref_mod
from . import __version__


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


def cmd_implode(args) -> int:
    tree = Path(args.tree)
    if not tree.is_dir():
        print("no such directory: %s" % tree, file=sys.stderr)
        return 2
    try:
        result = implode_mod.implode(tree, Path(args.out), force=args.force)
    except (FileExistsError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(implode_mod.render(result))
    return 1 if result.warnings else 0


def cmd_roundtrip(args) -> int:
    path = Path(args.file)
    if not path.is_file():
        print("no such file: %s" % path, file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="unifold-roundtrip-") as work:
        result = implode_mod.roundtrip(path, Path(work))
    print(implode_mod.render_roundtrip(result))
    return 0 if result.ok else 1


def cmd_xref(args) -> int:
    root = Path(args.workspace)
    if not root.is_dir():
        print("no such directory: %s" % root, file=sys.stderr)
        return 2
    # Data questions -- which components use this entity or field -- are
    # answered from the exploded properties, not from ProcScript.
    if args.entity or args.field or args.component or args.data:
        data = usage_mod.build(root)
        if args.json:
            print(usage_mod.to_json(data))
        elif args.entity:
            print(usage_mod.render_lookup(data, args.entity, "entity"))
        elif args.field:
            print(usage_mod.render_lookup(data, args.field, "field"))
        elif args.component:
            print(usage_mod.render_component(data, args.component))
            print(xref_mod.render_component(xref_mod.build(root), args.component))
        else:
            print(usage_mod.render(data))
        return 0

    index = xref_mod.build(root)
    if args.json:
        print(xref_mod.to_json(index))
    elif args.symbol:
        print(xref_mod.render_symbol(index, args.symbol))
    else:
        print(xref_mod.render(
            index,
            show_unresolved=not args.uncalled,
            show_uncalled=not args.unresolved,
        ))
    return 0


def cmd_index(args) -> int:
    root = Path(args.workspace)
    if not root.is_dir():
        print("no such directory: %s" % root, file=sys.stderr)
        return 2
    if args.stdout:
        print(workspace_mod.render(root))
        return 0
    target = workspace_mod.write(root, Path(args.out) if args.out else None)
    print("wrote %s" % target)
    return 0


def cmd_mcp(args) -> int:
    return mcp_mod.serve()


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

    i = sub.add_parser(
        "implode",
        help="rebuild an export file from an exploded tree",
        description=(
            "Reads a tree produced by `explode` and writes a Uniface export "
            "file. It does NOT import anything -- importing the result into "
            "your repository is a separate act you perform in the IDE. Run "
            "`unifold roundtrip` on the original export first to confirm "
            "unifold can reproduce it faithfully."
        ),
    )
    i.add_argument("tree", help="directory produced by `unifold explode`")
    i.add_argument("out", help="export file to write")
    i.add_argument("--force", action="store_true", help="overwrite the output file")
    i.set_defaults(func=cmd_implode)

    r = sub.add_parser(
        "roundtrip",
        help="prove an export survives explode -> implode unchanged",
        description=(
            "Explodes an export, implodes it straight back, and compares the "
            "two element by element with DAT values checked byte for byte. "
            "Everything happens in a scratch directory; nothing is kept. Exit "
            "code 0 means faithful. Run this on your own exports before "
            "trusting implode with them."
        ),
    )
    r.add_argument("file", help="path to a Uniface export file")
    r.set_defaults(func=cmd_roundtrip)

    x = sub.add_parser(
        "xref",
        help="cross-reference ProcScript across exploded trees",
        description=(
            "Indexes entry/operation/trigger definitions and call/activate "
            "references across a whole workspace, so you can trace a library "
            "proc across components -- the question the IDE cannot answer. "
            "A workspace is any directory containing exploded trees."
        ),
    )
    x.add_argument("workspace", help="directory containing exploded tree(s)")
    x.add_argument("--symbol", metavar="NAME",
                   help="show where one ProcScript name is defined and called")
    x.add_argument("--entity", metavar="NAME",
                   help="show which components use an entity")
    x.add_argument("--field", metavar="NAME",
                   help="show which components use a field")
    x.add_argument("--component", metavar="NAME",
                   help="show what one component uses and calls")
    x.add_argument("--data", action="store_true",
                   help="summarise entity and field usage instead of code")
    x.add_argument("--unresolved", action="store_true",
                   help="only what is called but not defined here")
    x.add_argument("--uncalled", action="store_true",
                   help="only entries nothing here calls")
    x.add_argument("--json", action="store_true", help="machine-readable output")
    x.set_defaults(func=cmd_xref)

    i2 = sub.add_parser(
        "index",
        help="write a navigable Markdown overview of a workspace",
        description=(
            "Generates INDEX.md: every component and library proc with its "
            "code files and line counts, the data model in use, and external "
            "dependencies -- with relative links that work in an editor, on "
            "GitHub and in a pull request. A derived view; regenerate it after "
            "re-exploding rather than editing it."
        ),
    )
    i2.add_argument("workspace", help="directory containing exploded tree(s)")
    i2.add_argument("--out", metavar="FILE",
                    help="write somewhere other than WORKSPACE/INDEX.md")
    i2.add_argument("--stdout", action="store_true",
                    help="print instead of writing a file")
    i2.set_defaults(func=cmd_index)

    m = sub.add_parser(
        "mcp",
        help="run a read-only MCP server over stdio",
        description=(
            "Serves workspace queries to an AI assistant over the Model "
            "Context Protocol, so it can trace library procs and analyse "
            "impact without you running commands and pasting output. "
            "Read-only: every tool answers a question, none writes anything."
        ),
    )
    m.set_defaults(func=cmd_mcp)

    return parser


def describe_failure(exc: Exception) -> str:
    """Turn an expected failure into something worth reading.

    A truncated export, a file that is not an export at all, or a hand-edited
    sidecar are all things users hit. A stack trace tells them nothing they can
    act on, so each gets a message naming the likely cause.
    """
    if isinstance(exc, ET.ParseError):
        return (
            "This file is not valid XML (%s).\n"
            "A Uniface export can end up truncated if the export was "
            "interrupted or the file was copied\npartially. Try re-exporting, "
            "and run `unifold probe` on it to see how far it parses." % exc
        )
    if isinstance(exc, json.JSONDecodeError):
        return (
            "%s is corrupt (%s).\n"
            "It is written by `unifold explode` and is not meant to be edited "
            "by hand. Re-run explode\nto regenerate the tree."
            % (explode_mod.SIDECAR, exc)
        )
    if isinstance(exc, UnicodeDecodeError):
        return (
            "This file could not be decoded as text (%s).\n"
            "Uniface exports are UTF-8. A binary or compiled object -- a .frm "
            "or .svc, say -- is not\nan export and cannot be read." % exc
        )
    return "%s: %s" % (type(exc).__name__, exc)


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except implode_mod.UnsafePath as exc:
        print("Refused for safety: %s" % exc, file=sys.stderr)
        return 3
    except (ET.ParseError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(describe_failure(exc), file=sys.stderr)
        return 2
    except OSError as exc:
        print("Could not read or write a file: %s" % exc, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
