"""Cross-reference ProcScript across a whole exploded workspace.

The IDE is good at navigating one component and has no answer at all for
questions that span components: who calls this library proc, what does this
component depend on, is this entry still used by anything. Those are the
questions that make tracing painful, and they are cheap once the code is on
disk.

Definition and reference forms are taken from real exports, not invented:

    entry OccurrenceSetFieldColors      definition
    operation exec                      definition
    trigger detail                      definition
    call processLayout(vLayout)         reference
    activate "USYSSTAT".setState(...)   reference

A workspace is any directory containing exploded trees, so one bulk export or
fifty separate ones both work. Paths are reported as
`export/TABLE/OBJECT/COLUMN.proc:line`, which is enough to open the right file.
"""

from __future__ import annotations

import io
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

DEF_RE = re.compile(
    r"^\s*(entry|operation|trigger)\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE
)
CALL_RE = re.compile(r"\bcall\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.IGNORECASE)
ACTIVATE_RE = re.compile(
    r"\bactivate\s+\"([^\"]+)\"\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE
)

# Triggers fire from the Uniface runtime, and operations are activated from
# other components or from outside the workspace entirely. Neither being
# uncalled is evidence of anything. Only `entry` is a fair dead-code candidate.
DEAD_CODE_KINDS = ("entry",)


def strip_comment(line: str) -> str:
    """Drop a trailing ProcScript comment, respecting quoted strings."""
    in_quote = False
    for index, char in enumerate(line):
        if char == '"':
            in_quote = not in_quote
        elif char == ";" and not in_quote:
            return line[:index]
    return line


@dataclass(frozen=True)
class Site:
    path: str          # workspace-relative, forward slashes
    line: int

    @property
    def owner(self) -> str:
        """`export/TABLE/OBJECT/COLUMN` for display, best effort."""
        return self.path[:-len(".proc")] if self.path.endswith(".proc") else self.path

    @property
    def object_name(self) -> str:
        parts = self.path.split("/")
        return parts[-2] if len(parts) >= 2 else "?"

    def __str__(self) -> str:
        return "%s:%d" % (self.path, self.line)


@dataclass
class Definition:
    kind: str
    name: str
    site: Site


@dataclass
class Reference:
    kind: str          # "call" or "activate"
    name: str          # operation or entry name
    service: object    # component named in an activate, else None
    site: Site


@dataclass
class Index:
    root: str = ""
    definitions: list = field(default_factory=list)
    references: list = field(default_factory=list)
    files: int = 0

    def defs_by_name(self) -> dict:
        out = defaultdict(list)
        for d in self.definitions:
            out[d.name.lower()].append(d)
        return out

    def refs_by_name(self) -> dict:
        out = defaultdict(list)
        for r in self.references:
            out[r.name.lower()].append(r)
        return out

    def unresolved(self) -> list:
        """References with no definition in the workspace."""
        known = set(self.defs_by_name())
        seen, out = set(), []
        for ref in self.references:
            key = ref.name.lower()
            if key not in known and key not in seen:
                seen.add(key)
                out.append(ref)
        return sorted(out, key=lambda r: r.name.lower())

    def uncalled(self) -> list:
        """Definitions nothing in the workspace references."""
        called = set(self.refs_by_name())
        return sorted(
            (d for d in self.definitions
             if d.kind.lower() in DEAD_CODE_KINDS and d.name.lower() not in called),
            key=lambda d: d.name.lower(),
        )


def scan_file(path: Path, relative: str, index: Index) -> None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return
    index.files += 1
    for number, raw in enumerate(text.split("\n"), 1):
        line = strip_comment(raw)
        if not line.strip():
            continue
        site = Site(relative, number)

        match = DEF_RE.match(line)
        if match:
            index.definitions.append(
                Definition(match.group(1).lower(), match.group(2), site)
            )

        for hit in CALL_RE.finditer(line):
            index.references.append(Reference("call", hit.group(1), None, site))

        for hit in ACTIVATE_RE.finditer(line):
            index.references.append(
                Reference("activate", hit.group(2), hit.group(1), site)
            )


def build(root: Path) -> Index:
    index = Index(root=str(root))
    for path in sorted(root.rglob("*.proc")):
        relative = path.relative_to(root).as_posix()
        scan_file(path, relative, index)
    return index


def lookup(index: Index, name: str) -> tuple:
    key = name.lower()
    definitions = [d for d in index.definitions if d.name.lower() == key]
    references = [r for r in index.references if r.name.lower() == key]
    return definitions, references


def render_symbol(index: Index, name: str) -> str:
    definitions, references = lookup(index, name)
    out = io.StringIO()
    out.write("symbol   %s\n\n" % name)
    if not definitions and not references:
        out.write("Not found in this workspace. It may live in a component that "
                  "has not been\nexported, or the name may be spelled differently.\n")
        return out.getvalue()

    if definitions:
        out.write("Defined in %d place(s)\n" % len(definitions))
        for d in definitions:
            out.write("  %-10s %s\n" % (d.kind, d.site))
    else:
        out.write("Defined: nowhere in this workspace.\n"
                  "         Likely an external or unexported library proc.\n")

    out.write("\nCalled from %d place(s)\n" % len(references))
    if not references:
        out.write("  (nothing in this workspace calls it)\n")
    for r in sorted(references, key=lambda r: (r.site.path, r.site.line)):
        via = ' via "%s"' % r.service if r.service else ""
        out.write("  %-8s %s%s\n" % (r.kind, r.site, via))
    return out.getvalue()


def render_component(index: Index, name: str) -> str:
    """What one object defines and what it calls out to."""
    wanted = name.lower()
    definitions = [d for d in index.definitions
                   if d.site.object_name.lower() == wanted]
    references = [r for r in index.references
                  if r.site.object_name.lower() == wanted]

    out = io.StringIO()
    out.write("\nCode in %s\n" % name)
    if not definitions and not references:
        out.write("  No ProcScript found for that name in this workspace.\n")
        return out.getvalue()

    plural = {"entry": "entries", "operation": "operations", "trigger": "triggers"}
    by_kind = defaultdict(set)
    for d in definitions:
        by_kind[d.kind].add(d.name)
    out.write("  defines: %s\n" % (", ".join(
        "%d %s" % (len(v), k if len(v) == 1 else plural.get(k, k + "s"))
        for k, v in sorted(by_kind.items())
    ) or "nothing"))

    outgoing = sorted({(r.service, r.name) for r in references})
    out.write("  calls out to %d distinct target(s)\n" % len(outgoing))
    for service, target in outgoing:
        out.write("    %s%s\n" % (('"%s".' % service) if service else "", target))
    return out.getvalue()


def render(index: Index, show_unresolved: bool = True,
           show_uncalled: bool = True) -> str:
    out = io.StringIO()
    out.write("workspace   %s\n" % index.root)
    out.write("proc files  %d\n" % index.files)
    out.write("definitions %d\n" % len(index.definitions))
    out.write("references  %d\n" % len(index.references))

    kinds = defaultdict(int)
    for d in index.definitions:
        kinds[d.kind] += 1
    if kinds:
        out.write("            %s\n" % ", ".join(
            "%s %d" % (k, n) for k, n in sorted(kinds.items())
        ))

    if show_unresolved:
        unresolved = index.unresolved()
        out.write("\n" + "=" * 70 + "\n")
        out.write("CALLED BUT NOT DEFINED HERE (%d)\n" % len(unresolved))
        out.write("=" * 70 + "\n")
        out.write("Expected for global library procs and services you have not "
                  "exported.\n")
        for ref in unresolved:
            via = ' via "%s"' % ref.service if ref.service else ""
            out.write("  %-28s first seen %s%s\n" % (ref.name, ref.site, via))

    if show_uncalled:
        uncalled = index.uncalled()
        out.write("\n" + "=" * 70 + "\n")
        out.write("ENTRIES NOTHING HERE CALLS (%d)\n" % len(uncalled))
        out.write("=" * 70 + "\n")
        out.write("Dead-code candidates only. An entry may still be called from a\n"
                  "component outside this workspace, so confirm before deleting.\n"
                  "Triggers and operations are excluded: the runtime and other\n"
                  "components invoke those, so silence here proves nothing.\n")
        for d in uncalled:
            out.write("  %-28s %s\n" % (d.name, d.site))
    return out.getvalue()


def to_json(index: Index) -> str:
    return json.dumps({
        "workspace": index.root,
        "files": index.files,
        "definitions": [
            {"kind": d.kind, "name": d.name,
             "path": d.site.path, "line": d.site.line}
            for d in index.definitions
        ],
        "references": [
            {"kind": r.kind, "name": r.name, "service": r.service,
             "path": r.site.path, "line": r.site.line}
            for r in index.references
        ],
        "unresolved": [r.name for r in index.unresolved()],
        "uncalled": [d.name for d in index.uncalled()],
    }, indent=2)
