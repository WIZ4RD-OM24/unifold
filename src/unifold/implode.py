"""Rebuild a Uniface export from an exploded tree, and prove the round trip.

`implode` writes an XML file. It does **not** touch your Uniface repository --
importing the result is a separate, deliberate act you perform in the IDE. That
boundary is the whole safety story: `unifold` never has write access to
anything of yours.

Fidelity is checked by reconstruction, not by faith. `roundtrip` explodes an
export, implodes it straight back, and compares the two documents element by
element, attribute by attribute, and -- crucially -- with `DAT` text compared
byte for byte rather than whitespace-normalised. If that reports clean on your
own exports, `implode` can reproduce them; if it does not, you should not be
importing anything it writes.

Byte-identical output is not the goal and is not achievable: the original's
inter-element line breaks and attribute wrapping are cosmetic and unrecorded.
Semantic identity -- same elements, same attributes, same values, same order --
is the goal, because that is what Uniface actually imports.
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from .explode import (MANIFEST, SIDECAR, XML_NS, explode,
                      read_properties, read_text)
from .probe import prepare

DEFAULT_PROLOG = (
    "<?xml version='1.0' encoding='UTF-8' ?>\r\n"
    "<!-- Created by Uniface - (C) Uniface B.V. All rights reserved -->\r\n"
    "<!DOCTYPE UNIFACE PUBLIC \"UNIFACE.DTD\" \"UNIFACE.DTD\">\r\n"
)
PLACEHOLDER_RE = re.compile(r"\[\[([A-Za-z_][A-Za-z0-9_.-]*)\]\]")


def escape_text(value: str, entities) -> str:
    """XML-escape, then restore Uniface entity references from placeholders.

    Order matters: a literal ampersand must become `&amp;` before placeholders
    are turned into `&name;`, or the entity references would be escaped too.
    """
    out = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    known = set(entities)

    def restore(match):
        name = match.group(1)
        return "&%s;" % name if name in known else match.group(0)

    return PLACEHOLDER_RE.sub(restore, out)


def escape_attr(value: str) -> str:
    return (value.replace("&", "&amp;").replace("<", "&lt;")
                 .replace(">", "&gt;").replace('"', "&quot;"))


def attr_name(name: str) -> str:
    """Turn ElementTree's namespaced key back into its source form."""
    return "xml:" + name[len(XML_NS):] if name.startswith(XML_NS) else name


def render_attrs(attrs: dict) -> str:
    parts = ["%s=\"%s\"" % (attr_name(k), escape_attr(v)) for k, v in attrs.items()]
    return (" " + " ".join(parts)) if parts else ""


@dataclass
class ImplodeResult:
    tree_dir: str
    out_file: str
    tables: int = 0
    occurrences: int = 0
    columns: int = 0
    bytes_written: int = 0
    warnings: list = field(default_factory=list)


class UnsafePath(ValueError):
    """A sidecar asked for a file outside the tree it belongs to."""


def contained(root: Path, *parts, source: str = SIDECAR) -> Path:
    """Resolve a sidecar-supplied path under `root`, refusing any escape.

    Exploded trees get shared -- committed to git, sent for review, copied off a
    network drive -- so `_unifold.json` is untrusted input even though `explode`
    normally writes it. Without this check a hand-edited sidecar could point at
    `../../../.ssh/id_rsa` and `implode` would quietly read it into the export.
    """
    root_resolved = root.resolve()
    target = root_resolved
    for part in parts:
        target = target / (part or "")
    target = target.resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError:
        raise UnsafePath(                       # noqa: B904 -- chain is noise
            "%s refers to %s, which is outside %s. Refusing to read it.\n"
            "Input from an untrusted source can use this to pull private files "
            "into\nsomewhere they do not belong."
            % (source, "/".join(str(p) for p in parts if p), root)
        ) from None
    return target


def load_sidecar(tree_dir: Path) -> dict:
    path = tree_dir / SIDECAR
    if not path.is_file():
        raise FileNotFoundError(
            "%s has no %s -- it was not produced by `unifold explode`, or the "
            "file was deleted. implode cannot reconstruct the repository schema "
            "without it." % (tree_dir, SIDECAR)
        )
    return json.loads(path.read_text(encoding="utf-8"))


def build(tree_dir: Path) -> tuple:
    """Reconstruct the export text. Returns (text, ImplodeResult-ish counters)."""
    sidecar = load_sidecar(tree_dir)
    entities = sidecar.get("entities", [])
    warnings: list = []

    out = io.StringIO()
    out.write(sidecar.get("prolog") or DEFAULT_PROLOG)
    out.write("<UNIFACE%s>\n" % render_attrs(sidecar.get("root", {})))

    # Grouped by TABLE block, not by table name: an export may repeat the same
    # DSC name across several blocks, and grouping by name duplicates every row.
    by_table: dict = {}
    for occ in sidecar.get("occurrences", []):
        by_table.setdefault(occ.get("table_index", 0), []).append(occ)

    columns_written = 0
    for table_index, table in enumerate(sidecar.get("tables", [])):
        out.write("<TABLE>\n")
        out.write("<DSC%s>\n" % render_attrs(table.get("dsc", {})))
        for fld in table.get("flds", []):
            out.write("<FLD%s />\n" % render_attrs(fld))
        out.write("</DSC>\n")

        for occ in by_table.get(table_index, []):
            base = contained(tree_dir, occ.get("dir"))
            properties = read_properties(contained(tree_dir, occ.get("dir"),
                                                   "properties.txt"))
            out.write("<OCC>\n")
            for column in occ.get("columns", []):
                name = column["name"]
                store = column.get("store")
                if store == "empty":
                    value = column.get("value", "")
                elif store == "inline":
                    if name not in properties:
                        warnings.append(
                            "%s: column %s missing from properties.txt; wrote it empty"
                            % (occ["dir"], name)
                        )
                    value = properties.get(name, "")
                else:
                    target = contained(tree_dir, occ.get("dir"),
                                       column.get("file"))
                    if target.is_file():
                        value = read_text(target)
                    else:
                        warnings.append(
                            "%s: file %s missing; wrote column %s empty"
                            % (occ["dir"], column.get("file"), name)
                        )
                        value = ""
                attrs = {"name": name}
                attrs.update(column.get("attrs", {}))
                out.write("<DAT%s>%s</DAT>\n"
                          % (render_attrs(attrs), escape_text(value, entities)))
                columns_written += 1
            out.write("</OCC>\n")
        out.write("</TABLE>\n")

    out.write("</UNIFACE>\n")
    counters = (
        len(sidecar.get("tables", [])),
        len(sidecar.get("occurrences", [])),
        columns_written,
        warnings,
    )
    return out.getvalue(), counters


def implode(tree_dir: Path, out_file: Path, force: bool = False) -> ImplodeResult:
    if out_file.exists() and not force:
        raise FileExistsError(
            "%s already exists. Pass --force to overwrite it." % out_file
        )
    text, (tables, occurrences, columns, warnings) = build(tree_dir)
    # Uniface writes a UTF-8 BOM; matching it keeps its own importer happy.
    data = b"\xef\xbb\xbf" + text.encode("utf-8")
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_bytes(data)
    return ImplodeResult(
        tree_dir=str(tree_dir), out_file=str(out_file), tables=tables,
        occurrences=occurrences, columns=columns, bytes_written=len(data),
        warnings=warnings,
    )


# --------------------------------------------------------------------------
# Fidelity checking
# --------------------------------------------------------------------------

def flatten(raw: bytes) -> list:
    """(path, attrs, text) for every element, in document order.

    Text is compared verbatim for DAT and ignored elsewhere, because only DAT
    carries content -- everything else holds layout whitespace.
    """
    root = ET.fromstring(prepare(raw)[0])
    rows: list = []

    def walk(elem, path):
        here = "%s/%s" % (path, elem.tag) if path else elem.tag
        text = elem.text if elem.tag == "DAT" else None
        rows.append((here, dict(elem.attrib), text))
        for child in elem:
            walk(child, here)

    walk(root, "")
    return rows


def verify(original: bytes, rebuilt: bytes) -> list:
    """Return a list of differences. Empty means a faithful round trip."""
    left, right = flatten(original), flatten(rebuilt)
    problems: list = []

    if len(left) != len(right):
        problems.append(
            "element count differs: original %d, rebuilt %d" % (len(left), len(right))
        )

    for index, (a, b) in enumerate(zip(left, right)):
        apath, aattrs, atext = a
        bpath, battrs, btext = b
        if apath != bpath:
            problems.append("element %d: path %s -> %s" % (index, apath, bpath))
            break                      # trees diverged; later diffs are noise
        if aattrs != battrs:
            only_a = {k: v for k, v in aattrs.items() if battrs.get(k) != v}
            only_b = {k: v for k, v in battrs.items() if aattrs.get(k) != v}
            problems.append("%s: attributes differ, original %s vs rebuilt %s"
                            % (apath, only_a, only_b))
        if (atext or "") != (btext or ""):
            problems.append("%s[%s]: text differs (%d chars vs %d)"
                            % (apath, aattrs.get("name", "?"),
                               len(atext or ""), len(btext or "")))
        if len(problems) > 40:
            problems.append("... further differences suppressed")
            break
    return problems


@dataclass
class RoundTripResult:
    source: str
    problems: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    files: int = 0
    occurrences: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems and not self.warnings


def roundtrip(path: Path, work_dir: Path) -> RoundTripResult:
    """explode -> implode -> compare, entirely in a scratch directory."""
    tree = work_dir / "tree"
    rebuilt = work_dir / "rebuilt.xml"
    exploded = explode(path, tree, force=True)
    result = implode(tree, rebuilt, force=True)
    problems = verify(path.read_bytes(), rebuilt.read_bytes())
    return RoundTripResult(
        source=str(path), problems=problems, warnings=result.warnings,
        files=len(exploded.files), occurrences=result.occurrences,
    )


def render(result: ImplodeResult) -> str:
    out = io.StringIO()
    out.write("tree     %s\n" % result.tree_dir)
    out.write("out      %s\n" % result.out_file)
    out.write("\nwrote %d bytes: %d table(s), %d occurrence(s), %d column(s)\n"
              % (result.bytes_written, result.tables,
                 result.occurrences, result.columns))
    for warning in result.warnings:
        out.write("  ! %s\n" % warning)
    out.write(
        "\nThis file has not been imported anywhere. Import it yourself in the "
        "Uniface\nIDE when you are ready -- and run `unifold roundtrip` on the "
        "original export\nfirst if you have not already.\n"
    )
    return out.getvalue()


def render_roundtrip(result: RoundTripResult) -> str:
    out = io.StringIO()
    out.write("source   %s\n" % result.source)
    out.write("exploded %d files, %d occurrence(s)\n"
              % (result.files, result.occurrences))
    for warning in result.warnings:
        out.write("  ! %s\n" % warning)
    if not result.problems:
        out.write("\nVERDICT: faithful -- every element, attribute and value "
                  "survived the round trip.\n")
        return out.getvalue()
    out.write("\nVERDICT: NOT faithful -- %d difference(s). Do not import "
              "imploded output for this export.\n\n" % len(result.problems))
    for problem in result.problems:
        out.write("  %s\n" % problem)
    return out.getvalue()
