"""Decode Uniface packed lists into something readable.

Uniface stores a lot of structure as delimited strings rather than as separate
columns. Two forms show up in exports, and both are unreadable as stored:

    WINPROP: CAPTION=<uSEP>CANRESIZE=<uSEP>MODAL=T<uSEP>SPLIT=
    FORMPIC: <uFRM>TYP=F<uSEP>NAM=LASTNAME<uSEP>WID=28<uSEP>HEI=1<uFRM>

A list separates entries with `uSEP`. Nesting works by prefixing `uNOT` to every
delimiter of the embedded list, so a delimiter carrying one `uNOT` sits one
level deeper than a bare one; decoding strips a level and recurses. `FORMPIC` is
the form picture: `uFRM` pairs wrap a widget descriptor and the text between
them is genuine layout whitespace, which is why the sketch below can reconstruct
roughly what the form looks like.

Everything here is **display only**. Decoded views are written as extra files;
the raw values remain the source of truth, and `implode` never reads these. That
keeps round-trip fidelity a property of the design rather than something to
re-verify each time this module changes.
"""

from __future__ import annotations

import io
import re

from .probe import placeholder_for

SEP = placeholder_for("uSEP")
NOT = placeholder_for("uNOT")
FRM = placeholder_for("uFRM")

# A delimiter is any run of uNOT markers followed by uSEP. The run length is the
# nesting depth: bare uSEP is the outermost list.
DELIM_RE = re.compile(r"((?:%s)*)%s" % (re.escape(NOT), re.escape(SEP)))
FRAME_RE = re.compile(r"%s(.*?)%s" % (re.escape(FRM), re.escape(FRM)), re.S)
PAIR_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.\-]*)=(.*)$", re.S)

MAX_DEPTH = 12


def is_packed(value: str) -> bool:
    return bool(value) and (SEP in value or FRM in value)


def split_top(value: str) -> list:
    """Split on outermost delimiters only, leaving deeper ones intact."""
    parts, last = [], 0
    for match in DELIM_RE.finditer(value):
        if match.group(1):          # nested delimiter, not ours to split on
            continue
        parts.append(value[last:match.start()])
        last = match.end()
    parts.append(value[last:])
    return parts


def unnest(value: str) -> str:
    """Remove one level of nesting from every delimiter in the text."""
    return DELIM_RE.sub(
        lambda m: m.group(1)[len(NOT):] + SEP if m.group(1) else SEP, value
    )


def parse(value: str, depth: int = 0):
    """Parse a packed list into nested lists of strings."""
    entries = []
    for part in split_top(value):
        if depth < MAX_DEPTH and DELIM_RE.search(part):
            entries.append(parse(unnest(part), depth + 1))
        else:
            entries.append(part)
    return entries


def as_pairs(entries: list):
    """Return [(key, value)] if every string entry looks like KEY=VALUE."""
    pairs = []
    for entry in entries:
        if not isinstance(entry, str):
            return None
        match = PAIR_RE.match(entry)
        if not match:
            return None
        pairs.append((match.group(1), match.group(2)))
    return pairs


def render_entries(entries: list, indent: str = "  ") -> str:
    out = io.StringIO()
    pairs = as_pairs(entries)
    if pairs is not None:
        width = max((len(k) for k, _ in pairs), default=0)
        for key, value in pairs:
            out.write("%s%-*s = %s\n" % (indent, width, key, value or "(empty)"))
        return out.getvalue()
    for position, entry in enumerate(entries, 1):
        if isinstance(entry, str):
            out.write("%s[%d] %s\n" % (indent, position, entry or "(empty)"))
        else:
            out.write("%s[%d]\n" % (indent, position))
            out.write(render_entries(entry, indent + "  "))
    return out.getvalue()


def frame_props(block: str) -> dict:
    props = {}
    for entry in split_top(block):
        match = PAIR_RE.match(entry)
        if match:
            props[match.group(1)] = match.group(2)
    return props


def render_formpic(value: str) -> str:
    """A rough visual sketch of the form, plus the widget descriptors."""
    widgets = []

    def token(match):
        props = frame_props(match.group(1))
        widgets.append(props)
        name = props.get("NAM", "?")
        label = "[%s]" % name
        try:
            width = int(props.get("WID", "0"))
        except ValueError:
            width = 0
        # Pad to the declared width so the sketch keeps the form's proportions.
        return label.ljust(width) if width > len(label) else label

    sketch = FRAME_RE.sub(token, value)

    out = io.StringIO()
    out.write("Layout sketch (widths approximate the real geometry)\n")
    out.write("-" * 68 + "\n")
    for line in sketch.split("\n"):
        out.write("  %s\n" % line.rstrip())
    if widgets:
        out.write("\nWidgets\n")
        out.write("-" * 68 + "\n")
        keys = []
        for props in widgets:
            for key in props:
                if key not in keys:
                    keys.append(key)
        widths = {
            k: max([len(k)] + [len(p.get(k) or "-") for p in widgets]) for k in keys
        }
        out.write("  " + "  ".join(k.ljust(widths[k]) for k in keys).rstrip() + "\n")
        for props in widgets:
            row = "  ".join(
                (props.get(k) or "-").ljust(widths[k]) for k in keys
            )
            out.write("  " + row.rstrip() + "\n")
    return out.getvalue()


def render_column(name: str, value: str) -> str:
    out = io.StringIO()
    out.write("%s\n%s\n" % (name, "=" * len(name)))
    if FRM in value:
        out.write(render_formpic(value))
    else:
        out.write(render_entries(parse(value)))
    return out.getvalue()


def render_occurrence(columns) -> str:
    """Decoded view of every packed column, or '' when there is nothing to show."""
    blocks = [
        render_column(column.name, column.value)
        for column in columns
        if is_packed(column.value)
    ]
    if not blocks:
        return ""
    header = (
        "Decoded view, generated by unifold.\n"
        "Read-only: implode ignores this file and uses the raw values, so\n"
        "edits here have no effect. Change the raw column instead.\n\n"
    )
    return header + "\n".join(blocks)
