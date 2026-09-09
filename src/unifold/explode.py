"""Turn a Uniface export into a readable, diffable directory tree.

The export is a set of repository tables, each row a development object and each
column either a scalar property or a slab of ProcScript. `explode` mirrors that
faithfully rather than inventing a prettier model on top of it:

    <out>/
      manifest.txt                 what this export contained, for humans
      _unifold.json                what implode needs, for machines
      UFORM/
        SHOWEMPLOYEES/
          properties.txt           every scalar column, sorted
          INIT.proc                one file per code-bearing column
          UDECLARATIONS.proc

Two decisions worth stating, because both are deliberate:

*No semantic renaming.* Table and column names are kept exactly as Uniface uses
them (`UXGROUP`, not `entity`). A component's triggers are simply the columns
that hold code. Anything friendlier would be a mapping we have not yet earned
the right to assert, and a wrong name is worse than an unfamiliar one.

*Content decides the split, not a hardcoded list.* A column becomes its own file
when its value is multi-line or long, and a property otherwise. `UFORM` alone
has 69 columns and the trigger set differs per table and per version, so a
curated list would rot; measuring the value does not.

Values are written verbatim -- no stripping, no added newlines -- because
`implode` has to reproduce them exactly. The readable tree is the source of
truth for content; `_unifold.json` carries only what the tree cannot express:
the repository schema blocks, document order, column order, and empty columns.
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from .probe import PROC_RE, placeholder_for, prepare

SIDECAR = "_unifold.json"
MANIFEST = "manifest.txt"
PROPERTIES = "properties.txt"

# A value at or under this length with no newline is a property, not a file.
INLINE_MAX = 200

# Columns that identify a row, best first. Uniface labels objects with these.
KEY_COLUMNS = (
    "ULABEL", "ULIBRARY", "U_FLAB", "U_GLAB", "U_VLAB", "U_TLAB", "UVAR",
)

UNSAFE = re.compile(r"[^A-Za-z0-9_.\-]+")
RESERVED = {
    "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5",
    "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5",
    "LPT6", "LPT7", "LPT8", "LPT9",
}

XML_NS = "{http://www.w3.org/XML/1998/namespace}"


def safe_name(value: str, fallback: str = "unnamed") -> str:
    """Make a path segment that is safe on Windows and stable across runs."""
    cleaned = UNSAFE.sub("_", (value or "").strip()).strip("._")
    if not cleaned:
        return fallback
    if cleaned.upper() in RESERVED:
        cleaned += "_"
    return cleaned[:80]


@dataclass
class Column:
    name: str
    value: str
    attrs: dict = field(default_factory=dict)   # DAT attributes besides name


@dataclass
class Occurrence:
    table: str
    key: str
    columns: list = field(default_factory=list)
    # Which TABLE block this row came from. An export may repeat the same DSC
    # name across several blocks -- the project sample does -- so the name
    # alone cannot put a row back where it belongs.
    table_index: int = 0

    def split(self):
        """Partition columns into (properties, blocks, empties), order kept."""
        properties, blocks, empties = [], [], []
        for column in self.columns:
            if not column.value or not column.value.strip():
                empties.append(column)
            elif "\n" in column.value or len(column.value) > INLINE_MAX:
                blocks.append(column)
            else:
                properties.append(column)
        return properties, blocks, empties


@dataclass
class Table:
    name: str
    dsc_attrs: dict = field(default_factory=dict)
    flds: list = field(default_factory=list)    # each FLD's attributes, in order


@dataclass
class Document:
    prolog: str = ""
    root_attrs: dict = field(default_factory=dict)
    entities: list = field(default_factory=list)
    tables: list = field(default_factory=list)
    occurrences: list = field(default_factory=list)   # document order


@dataclass
class ExplodeResult:
    source: str
    out_dir: str
    document: Document = field(default_factory=Document)
    files: list = field(default_factory=list)
    skipped_empty: int = 0

    @property
    def release(self):
        return self.document.root_attrs.get("release")

    @property
    def xmlengine(self):
        return self.document.root_attrs.get("xmlengine")

    @property
    def entities(self):
        return self.document.entities

    @property
    def tables(self):
        counts = {}
        for occ in self.document.occurrences:
            counts[occ.table_index] = counts.get(occ.table_index, 0) + 1
        return [(t.name, counts.get(i, 0))
                for i, t in enumerate(self.document.tables)]

    @property
    def occurrence_count(self) -> int:
        return len(self.document.occurrences)


def read(path: Path) -> Document:
    """Parse an export into everything needed to write it back out again."""
    raw = path.read_bytes()
    prepared, entities = prepare(raw)
    text = raw.decode("utf-8-sig", errors="replace")
    at = text.find("<UNIFACE")
    document = Document(
        prolog=text[:at] if at > 0 else "",
        entities=entities,
    )
    root = ET.fromstring(prepared)
    document.root_attrs = dict(root.attrib)

    for table in root.findall("TABLE"):
        dsc = table.find("DSC")
        if dsc is None:
            continue
        name = dsc.get("name") or "UNKNOWN"
        document.tables.append(Table(
            name=name,
            dsc_attrs=dict(dsc.attrib),
            flds=[dict(fld.attrib) for fld in dsc.findall("FLD")],
        ))
        for index, occ in enumerate(table.findall("OCC")):
            columns = []
            values = {}
            for dat in occ.findall("DAT"):
                col = dat.get("name")
                if not col:
                    continue
                attrs = {k: v for k, v in dat.attrib.items() if k != "name"}
                value = dat.text or ""
                columns.append(Column(col, value, attrs))
                values[col] = value
            document.occurrences.append(
                Occurrence(name, key_for(values, index), columns,
                           table_index=len(document.tables) - 1)
            )
    return document


def key_for(values: dict, index: int) -> str:
    for column in KEY_COLUMNS:
        candidate = (values.get(column) or "").strip()
        if candidate and "\n" not in candidate:
            return safe_name(candidate)
    return "occ_%03d" % (index + 1)


def extension_for(value: str) -> str:
    return ".proc" if PROC_RE.search(value) else ".txt"


def explode(path: Path, out_dir: Path, force: bool = False,
            dry_run: bool = False) -> ExplodeResult:
    if out_dir.exists() and any(out_dir.iterdir()) and not force and not dry_run:
        raise FileExistsError(
            "%s is not empty. Pass --force to write into it anyway." % out_dir
        )

    document = read(path)
    result = ExplodeResult(str(path), str(out_dir), document)

    planned: list = []            # (relative path, content)
    sidecar_occs: list = []
    seen: dict = {}

    # Directory names are assigned in sorted order so the tree is identical for
    # identical input; the sidecar records true document order for implode.
    order = sorted(
        range(len(document.occurrences)),
        key=lambda i: (document.occurrences[i].table, document.occurrences[i].key, i),
    )
    directories: dict = {}
    for i in order:
        occ = document.occurrences[i]
        slot = (occ.table, occ.key)
        seen[slot] = seen.get(slot, 0) + 1
        name = occ.key if seen[slot] == 1 else "%s__%d" % (occ.key, seen[slot])
        directories[i] = "%s/%s" % (safe_name(occ.table), name)

    for i, occ in enumerate(document.occurrences):
        base = directories[i]
        properties, blocks, empties = occ.split()
        result.skipped_empty += len(empties)

        if properties:
            body = io.StringIO()
            for column in sorted(properties, key=lambda c: c.name):
                body.write("%s: %s\n" % (column.name, column.value))
            planned.append(("%s/%s" % (base, PROPERTIES), body.getvalue()))

        entries = []
        used: set = set()
        for column in occ.columns:
            record = {"name": column.name}
            if column.attrs:
                record["attrs"] = column.attrs
            if column in empties:
                record["store"] = "empty"
                record["value"] = column.value
            elif column in properties:
                record["store"] = "inline"
            else:
                filename = "%s%s" % (safe_name(column.name), extension_for(column.value))
                while filename in used:          # two columns sanitising alike
                    filename = "_" + filename
                used.add(filename)
                record["store"] = "file"
                record["file"] = filename
                planned.append(("%s/%s" % (base, filename), column.value))
            entries.append(record)

        sidecar_occs.append({
            "table": occ.table,
            "table_index": occ.table_index,
            "dir": base,
            "columns": entries,
        })

    sidecar = {
        "note": "Written by unifold explode. implode reads this; humans need "
                "manifest.txt instead. Editing the .proc and properties.txt "
                "files is expected -- editing this file is not.",
        "prolog": document.prolog,
        "root": document.root_attrs,
        "entities": document.entities,
        "tables": [
            {"name": t.name, "dsc": t.dsc_attrs, "flds": t.flds}
            for t in document.tables
        ],
        "occurrences": sidecar_occs,
    }

    planned.insert(0, (SIDECAR, json.dumps(sidecar, indent=2, ensure_ascii=False)))
    planned.insert(0, (MANIFEST, render_manifest(result, planned)))
    result.files = [rel for rel, _ in planned]

    if not dry_run:
        for rel, content in planned:
            target = out_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="")

    return result


def render_manifest(result: ExplodeResult, planned: list) -> str:
    out = io.StringIO()
    out.write("Exploded by unifold\n")
    out.write("source        %s\n" % Path(result.source).name)
    out.write("release       %s\n" % (result.release or "(unknown)"))
    out.write("xmlengine     %s\n" % (result.xmlengine or "(unknown)"))
    out.write("tables        %d\n" % len(result.tables))
    out.write("occurrences   %d\n" % result.occurrence_count)
    out.write("files written %d\n" % (len(planned) + 1))
    out.write("\nTables\n")
    for name, count in result.tables:
        out.write("  %-12s %d occurrence(s)\n" % (name, count))
    if result.entities:
        out.write(
            "\nCustom entities from UNIFACE.DTD were replaced with placeholders,\n"
            "because the DTD is not shipped with exports and their real values\n"
            "are unknown. Text below therefore contains:\n"
        )
        for name in result.entities:
            out.write("  &%s; -> %s\n" % (name, placeholder_for(name)))
        out.write(
            "implode turns these back into entity references, so the round trip\n"
            "is exact without the real characters ever being needed.\n"
        )
    out.write(
        "\nColumn values are split by content: multi-line or longer than %d\n"
        "characters becomes its own file, everything else is a line in\n"
        "%s. Empty columns are omitted from the tree (%d of them) but\n"
        "recorded in %s so implode can restore them.\n"
        % (INLINE_MAX, PROPERTIES, result.skipped_empty, SIDECAR)
    )
    return out.getvalue()


def render(result: ExplodeResult, dry_run: bool = False) -> str:
    out = io.StringIO()
    verb = "would write" if dry_run else "wrote"
    out.write("source   %s\n" % result.source)
    out.write("out      %s\n" % result.out_dir)
    out.write("release  %s\n" % (result.release or "(unknown)"))
    out.write("\n%s %d files across %d table(s), %d occurrence(s)\n"
              % (verb, len(result.files), len(result.tables), result.occurrence_count))
    for rel in result.files:
        out.write("  %s\n" % rel)
    return out.getvalue()
