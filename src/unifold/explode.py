"""Turn a Uniface export into a readable, diffable directory tree.

The export is a set of repository tables, each row a development object and each
column either a scalar property or a slab of ProcScript. `explode` mirrors that
faithfully rather than inventing a prettier model on top of it:

    <out>/
      manifest.txt                 what this export contained
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
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from .probe import PROC_RE, placeholder_for, prepare

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


def safe_name(value: str, fallback: str = "unnamed") -> str:
    """Make a path segment that is safe on Windows and stable across runs."""
    cleaned = UNSAFE.sub("_", (value or "").strip()).strip("._")
    if not cleaned:
        return fallback
    if cleaned.upper() in RESERVED:
        cleaned += "_"
    return cleaned[:80]


@dataclass
class Occurrence:
    table: str
    key: str
    values: dict = field(default_factory=dict)

    def split(self):
        """Partition columns into (properties, code blocks), dropping empties."""
        properties: dict = {}
        blocks: dict = {}
        for name, value in self.values.items():
            if value is None or not value.strip():
                continue
            if "\n" in value or len(value) > INLINE_MAX:
                blocks[name] = value
            else:
                properties[name] = value.strip()
        return properties, blocks


@dataclass
class ExplodeResult:
    source: str
    out_dir: str
    release: object = None
    xmlengine: object = None
    entities: list = field(default_factory=list)
    tables: list = field(default_factory=list)   # (table, occurrence count)
    files: list = field(default_factory=list)    # paths relative to out_dir
    skipped_empty: int = 0

    @property
    def occurrence_count(self) -> int:
        return sum(n for _, n in self.tables)


def read(path: Path):
    """Parse an export into (root attributes, occurrences, custom entity names)."""
    prepared, entities = prepare(path.read_bytes())
    root = ET.fromstring(prepared)
    occurrences: list = []
    tables: list = []
    for table in root.findall("TABLE"):
        dsc = table.find("DSC")
        if dsc is None:
            continue
        name = dsc.get("name") or "UNKNOWN"
        occs = table.findall("OCC")
        tables.append((name, len(occs)))
        for index, occ in enumerate(occs):
            values = {}
            for dat in occ.findall("DAT"):
                col = dat.get("name")
                if col:
                    values[col] = dat.text or ""
            occurrences.append(Occurrence(name, key_for(values, index), values))
    return root.attrib, tables, occurrences, entities


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

    attrib, tables, occurrences, entities = read(path)
    result = ExplodeResult(
        source=str(path),
        out_dir=str(out_dir),
        release=attrib.get("release"),
        xmlengine=attrib.get("xmlengine"),
        entities=entities,
        tables=tables,
    )

    planned: list = []  # (relative path, content)

    # Sort so the tree is identical for identical input, whatever order the
    # export happened to serialise its rows in.
    seen: dict = {}
    for occ in sorted(occurrences, key=lambda o: (o.table, o.key)):
        # Disambiguate rows that share a key rather than silently overwriting.
        slot = (occ.table, occ.key)
        seen[slot] = seen.get(slot, 0) + 1
        directory = occ.key if seen[slot] == 1 else "%s__%d" % (occ.key, seen[slot])
        base = "%s/%s" % (safe_name(occ.table), directory)

        properties, blocks = occ.split()
        result.skipped_empty += len(occ.values) - len(properties) - len(blocks)

        if properties:
            body = io.StringIO()
            for name in sorted(properties):
                body.write("%s: %s\n" % (name, properties[name]))
            planned.append(("%s/properties.txt" % base, body.getvalue()))
        for name in sorted(blocks):
            value = blocks[name]
            filename = "%s%s" % (safe_name(name), extension_for(value))
            planned.append(("%s/%s" % (base, filename), value.strip("\n") + "\n"))

    planned.insert(0, ("manifest.txt", render_manifest(result, planned)))
    result.files = [rel for rel, _ in planned]

    if not dry_run:
        for rel, content in planned:
            target = out_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8", newline="\n")

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
        "\nColumn values are split by content: multi-line or longer than %d\n"
        "characters becomes its own file, everything else is a line in\n"
        "properties.txt. Empty columns are omitted (%d skipped).\n"
        % (INLINE_MAX, result.skipped_empty)
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
