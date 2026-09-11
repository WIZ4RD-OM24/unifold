"""Track which components use which entities and fields.

`xref` answers questions about code. This answers the other half: what breaks if
I change this field, which components touch this entity, is this model entity
used by anything at all. Impact analysis, in other words -- the question that
sends people clicking through the IDE one component at a time.

None of it needs ProcScript parsing. The repository already records the
relationships as columns, and `explode` has already written them out:

    UCTABLE   U_VLAB=model  U_TLAB=entity                     entity defined
    UCGROUP   U_VLAB=model  U_TLAB=entity  U_GLAB=group       entity defined
    UCFIELD   U_VLAB=model  U_TLAB=entity  U_FLAB=field       field defined
    UXGROUP   UFORM=component  ULABEL=entity  UBASE=model     entity used
    UXFIELD   UFORM=component  GRP=entity  ULABEL=field       field used

Entities are keyed `MODEL.ENTITY` and fields `MODEL.ENTITY.FIELD`, so two models
defining the same entity name stay distinct.
"""

from __future__ import annotations

import io
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .explode import PROPERTIES, read_properties

# table -> (kind, how to read it)
DEFINITION_TABLES = {"UCTABLE": "entity", "UCGROUP": "entity", "UCFIELD": "field"}
USAGE_TABLES = {"UXGROUP": "entity", "UXFIELD": "field"}


def qualify(*parts) -> str:
    return ".".join(p for p in parts if p)


def count(number: int, singular: str, plural: str) -> str:
    return "%d %s" % (number, singular if number == 1 else plural)


@dataclass(frozen=True)
class Site:
    path: str

    def __str__(self) -> str:
        return self.path


@dataclass
class Definition:
    kind: str          # "entity" or "field"
    key: str           # MODEL.ENTITY[.FIELD]
    model: str
    entity: str
    name: str          # entity or field name
    dtype: str
    site: Site


@dataclass
class Usage:
    kind: str          # "entity" or "field"
    key: str
    component: str
    model: str
    entity: str
    name: str
    site: Site


@dataclass
class DataIndex:
    root: str = ""
    definitions: list = field(default_factory=list)
    usages: list = field(default_factory=list)
    occurrences: int = 0

    def components(self) -> dict:
        out = defaultdict(list)
        for use in self.usages:
            out[use.component].append(use)
        return out

    def defined_keys(self) -> set:
        return {d.key for d in self.definitions}

    def used_keys(self) -> set:
        return {u.key for u in self.usages}

    def unused_definitions(self) -> list:
        used = self.used_keys()
        return sorted(
            (d for d in self.definitions if d.key not in used),
            key=lambda d: d.key,
        )

    def undefined_usages(self) -> list:
        """Used by a component, but the defining model was not exported."""
        defined = self.defined_keys()
        seen, out = set(), []
        for use in self.usages:
            if use.key not in defined and use.key not in seen:
                seen.add(use.key)
                out.append(use)
        return sorted(out, key=lambda u: u.key)


def read_definition(table: str, values: dict, site: Site):
    model = values.get("U_VLAB", "")
    entity = values.get("U_TLAB", "")
    if table == "UCFIELD":
        name = values.get("U_FLAB", "")
        if not name:
            return None
        return Definition("field", qualify(model, entity, name), model, entity,
                          name, values.get("U_DTYP", ""), site)
    name = entity or values.get("U_GLAB", "")
    if not name:
        return None
    return Definition("entity", qualify(model, name), model, name, name, "", site)


def read_usage(table: str, values: dict, site: Site):
    component = values.get("UFORM", "")
    model = values.get("UBASE", "")
    if table == "UXFIELD":
        entity = values.get("U_TLAB") or values.get("GRP", "")
        name = values.get("ULABEL", "")
        if not (component and name):
            return None
        return Usage("field", qualify(model, entity, name), component, model,
                     entity, name, site)
    entity = values.get("U_GLAB") or values.get("ULABEL", "")
    if not (component and entity):
        return None
    return Usage("entity", qualify(model, entity), component, model,
                 entity, entity, site)


def build(root: Path) -> DataIndex:
    index = DataIndex(root=str(root))
    for path in sorted(root.rglob(PROPERTIES)):
        parts = path.relative_to(root).as_posix().split("/")
        if len(parts) < 3:
            continue
        table = parts[-3]
        if table not in DEFINITION_TABLES and table not in USAGE_TABLES:
            continue
        values = read_properties(path)
        if not values:
            continue
        index.occurrences += 1
        site = Site(path.relative_to(root).as_posix())
        if table in DEFINITION_TABLES:
            record = read_definition(table, values, site)
            if record and record.key not in {d.key for d in index.definitions}:
                index.definitions.append(record)
        else:
            record = read_usage(table, values, site)
            if record:
                index.usages.append(record)
    return index


def match(index: DataIndex, name: str, kind: str):
    """Find definitions and usages by bare or qualified name, case-insensitive."""
    wanted = name.lower()

    def hit(key: str, bare: str) -> bool:
        return wanted in (key.lower(), bare.lower())

    definitions = [d for d in index.definitions
                   if d.kind == kind and hit(d.key, d.name)]
    usages = [u for u in index.usages if u.kind == kind and hit(u.key, u.name)]
    return definitions, usages


def render_lookup(index: DataIndex, name: str, kind: str) -> str:
    definitions, usages = match(index, name, kind)
    out = io.StringIO()
    out.write("%s   %s\n\n" % (kind, name))

    if not definitions and not usages:
        out.write("Not found in this workspace. The model or the components "
                  "using it may not\nhave been exported.\n")
        return out.getvalue()

    if definitions:
        out.write("Defined in %d place(s)\n" % len(definitions))
        for d in definitions:
            extra = " type %s" % d.dtype if d.dtype else ""
            out.write("  %-34s %s%s\n" % (d.key, d.site, extra))
    else:
        out.write("Defined: not in this workspace "
                  "(the application model was not exported).\n")

    by_component = defaultdict(list)
    for use in usages:
        by_component[use.component].append(use)

    out.write("\nUsed by %d component(s)\n" % len(by_component))
    if not by_component:
        out.write("  (no component in this workspace uses it)\n")
    for component in sorted(by_component):
        entries = by_component[component]
        out.write("  %-28s %s\n" % (component, entries[0].site))
    return out.getvalue()


def render_component(index: DataIndex, name: str) -> str:
    wanted = name.lower()
    entries = [u for u in index.usages if u.component.lower() == wanted]
    out = io.StringIO()
    out.write("component   %s\n\n" % name)
    if not entries:
        out.write("No data usage recorded for that component in this "
                  "workspace.\n")
        return out.getvalue()

    entities = sorted({u.entity for u in entries if u.kind == "entity"})
    out.write("Entities (%d)\n" % len(entities))
    for entity in entities:
        # De-duplicated: the same component can appear in several exports, and
        # listing each field once per export helps nobody.
        fields = sorted({u.name for u in entries
                         if u.kind == "field" and u.entity == entity})
        out.write("  %-24s %s\n"
                  % (entity, ", ".join(fields) if fields else "(no fields used)"))

    orphans = sorted({u.name for u in entries
                      if u.kind == "field" and u.entity not in entities})
    if orphans:
        out.write("\nFields whose entity is not recorded here (%d)\n" % len(orphans))
        out.write("  %s\n" % ", ".join(orphans))
    return out.getvalue()


def render(index: DataIndex) -> str:
    out = io.StringIO()
    components = index.components()
    out.write("workspace     %s\n" % index.root)
    out.write("rows scanned  %d\n" % index.occurrences)
    out.write("defined       %s, %s\n" % (
        count(sum(1 for d in index.definitions if d.kind == "entity"),
              "entity", "entities"),
        count(sum(1 for d in index.definitions if d.kind == "field"),
              "field", "fields"),
    ))
    out.write("components    %d\n" % len(components))

    shared = defaultdict(set)
    for use in index.usages:
        if use.kind == "entity":
            shared[use.key].add(use.component)
    multi = {k: v for k, v in shared.items() if len(v) > 1}

    out.write("\n" + "=" * 70 + "\n")
    out.write("ENTITIES USED BY MORE THAN ONE COMPONENT (%d)\n" % len(multi))
    out.write("=" * 70 + "\n")
    out.write("Change these carefully -- the blast radius is listed.\n")
    for key in sorted(multi):
        out.write("  %-34s %s\n" % (key, ", ".join(sorted(multi[key]))))

    undefined = index.undefined_usages()
    out.write("\n" + "=" * 70 + "\n")
    out.write("USED BUT NOT DEFINED HERE (%d)\n" % len(undefined))
    out.write("=" * 70 + "\n")
    out.write("Export the application model to resolve these.\n")
    for use in undefined:
        out.write("  %-34s used by %s\n" % (use.key, use.component))

    unused = index.unused_definitions()
    out.write("\n" + "=" * 70 + "\n")
    out.write("DEFINED BUT UNUSED HERE (%d)\n" % len(unused))
    out.write("=" * 70 + "\n")
    out.write("Candidates only: a component that uses them may not be in this\n"
              "workspace. Export more widely before concluding anything.\n")
    for d in unused:
        out.write("  %-34s %s\n" % (d.key, d.site))
    return out.getvalue()


def to_json(index: DataIndex) -> str:
    return json.dumps({
        "workspace": index.root,
        "definitions": [
            {"kind": d.kind, "key": d.key, "model": d.model, "entity": d.entity,
             "name": d.name, "type": d.dtype, "path": d.site.path}
            for d in index.definitions
        ],
        "usages": [
            {"kind": u.kind, "key": u.key, "component": u.component,
             "model": u.model, "entity": u.entity, "name": u.name,
             "path": u.site.path}
            for u in index.usages
        ],
        "undefined": [u.key for u in index.undefined_usages()],
        "unused": [d.key for d in index.unused_definitions()],
    }, indent=2)
