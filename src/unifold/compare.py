"""Compare two Uniface exports of the same object.

This answers the question the whole product rests on: when you export an
unchanged object twice, does the XML come back identical? If it does not, we
need to know whether the difference is real (content changed) or cosmetic
(attribute order, element order, whitespace, timestamps). Cosmetic churn is
exactly why raw export XML is useless in version control, and canonicalising it
away is the first thing `explode` has to do.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

# Attributes that change on every export regardless of content. Populated from
# real files via `unifold probe`; empty until we have measured a repository.
VOLATILE_ATTRS: set = set()


def canonical(elem: ET.Element, sort_children: bool, drop: set) -> str:
    """Serialise an element with attributes sorted and optional child sorting."""
    out = io.StringIO()

    def emit(node: ET.Element, depth: int) -> None:
        pad = "  " * depth
        attrs = sorted(
            (k, v) for k, v in node.attrib.items() if k not in drop
        )
        rendered = " ".join('%s="%s"' % (k, v) for k, v in attrs)
        head = node.tag + (" " + rendered if rendered else "")
        text = (node.text or "").strip()
        children = list(node)
        if sort_children:
            children = sorted(children, key=child_key)
        if not children and not text:
            out.write("%s<%s/>\n" % (pad, head))
            return
        out.write("%s<%s>\n" % (pad, head))
        if text:
            for line in text.splitlines():
                out.write("%s  %s\n" % (pad, line.rstrip()))
        for child in children:
            emit(child, depth + 1)
        out.write("%s</%s>\n" % (pad, node.tag))

    emit(elem, 0)
    return out.getvalue()


def child_key(node: ET.Element):
    """Stable sort key for sibling elements: tag, then all attribute values."""
    return (node.tag, tuple(sorted(node.attrib.items())), (node.text or "").strip()[:80])


@dataclass
class CompareResult:
    left: str
    right: str
    byte_identical: bool
    canonical_identical: bool
    sorted_identical: bool
    diff_lines: list = field(default_factory=list)

    @property
    def verdict(self) -> str:
        if self.byte_identical:
            return "identical bytes -- export is fully deterministic"
        if self.canonical_identical:
            return (
                "differs only in whitespace/attribute order -- cosmetic churn, "
                "canonicalisation is enough"
            )
        if self.sorted_identical:
            return (
                "differs only in sibling element ORDER -- exports are unstable; "
                "explode must impose a canonical ordering or every diff will be noise"
            )
        return "genuine content differences (or volatile fields not yet excluded)"


def compare(left: Path, right: Path, drop: set = None) -> CompareResult:
    drop = drop if drop is not None else VOLATILE_ATTRS
    lb, rb = left.read_bytes(), right.read_bytes()
    lroot = ET.fromstring(lb)
    rroot = ET.fromstring(rb)

    lcanon = canonical(lroot, sort_children=False, drop=drop)
    rcanon = canonical(rroot, sort_children=False, drop=drop)
    lsorted = canonical(lroot, sort_children=True, drop=drop)
    rsorted = canonical(rroot, sort_children=True, drop=drop)

    diff_lines: list = []
    if lsorted != rsorted:
        import difflib

        diff_lines = list(
            difflib.unified_diff(
                lsorted.splitlines(),
                rsorted.splitlines(),
                fromfile=str(left),
                tofile=str(right),
                lineterm="",
                n=2,
            )
        )[:400]

    return CompareResult(
        left=str(left),
        right=str(right),
        byte_identical=lb == rb,
        canonical_identical=lcanon == rcanon,
        sorted_identical=lsorted == rsorted,
        diff_lines=diff_lines,
    )


def render(result: CompareResult) -> str:
    out = io.StringIO()
    out.write("left   %s\n" % result.left)
    out.write("right  %s\n" % result.right)
    out.write("\nbyte identical       %s\n" % result.byte_identical)
    out.write("canonical identical  %s\n" % result.canonical_identical)
    out.write("order-free identical %s\n" % result.sorted_identical)
    out.write("\nVERDICT: %s\n" % result.verdict)
    if result.diff_lines:
        out.write("\n" + "=" * 78 + "\nREMAINING DIFFERENCES\n" + "=" * 78 + "\n")
        for line in result.diff_lines:
            out.write(line + "\n")
    return out.getvalue()
