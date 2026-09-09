"""Discover the structure of a Uniface export file.

We deliberately do not hard-code a schema. Rocket does not publish the 9.7
export schema in an accessible place, and guessing it would produce a parser
that silently mangles real repositories. Instead we read a real export and
report exactly what is in it: the element tree, attribute cardinalities, where
text content lives, and what that text looks like (ProcScript, base64, opaque
blob). The exploder is then written against a mapping derived from this report.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import re
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

MAX_SAMPLES = 5
SAMPLE_CHARS = 240

# ProcScript markers. Kept deliberately broad: this is a hint for a human
# reading the report, not a classification anything downstream depends on.
PROC_MARKERS = (
    r"\btrigger\b", r"\bentry\b", r"\bendentry\b", r"\boperation\b",
    r"\bendoperation\b", r"\bparams\b", r"\bendparams\b", r"\bvariables\b",
    r"\bendvariables\b", r"\bactivate\b", r"\bretrieve\b", r"\bstore\b",
    r"\bforentity\b", r"\bendfor\b", r"\bendif\b", r"\bnewinstance\b",
    r"\$status\b", r"\$procerror\b", r"\$dbocc\b",
)
PROC_RE = re.compile("|".join(PROC_MARKERS), re.IGNORECASE)
BASE64_RE = re.compile(r"^[A-Za-z0-9+/\s]{40,}={0,2}\s*$")
HEX_RE = re.compile(r"^[0-9A-Fa-f\s]{40,}$")


def classify(text: str) -> list[str]:
    """Label a chunk of text content. Labels are additive hints, not a taxonomy."""
    tags: list[str] = []
    stripped = text.strip()
    if not stripped:
        return ["empty"]
    if "\n" in stripped:
        tags.append("multiline")
    if PROC_RE.search(stripped):
        tags.append("procscript?")
    if BASE64_RE.match(stripped):
        # Confirm it actually decodes before claiming base64.
        try:
            base64.b64decode(stripped, validate=True)
            tags.append("base64")
        except (binascii.Error, ValueError):
            pass
    if HEX_RE.match(stripped):
        tags.append("hex?")
    if stripped.isdigit():
        tags.append("numeric")
    if len(stripped) > 2000:
        tags.append("large")
    if not tags:
        tags.append("text")
    return tags


@dataclass
class AttrProfile:
    name: str
    count: int = 0
    min_len: int = 10 ** 9
    max_len: int = 0
    distinct: Counter = field(default_factory=Counter)

    def observe(self, value: str) -> None:
        self.count += 1
        self.min_len = min(self.min_len, len(value))
        self.max_len = max(self.max_len, len(value))
        if len(self.distinct) < 50:
            self.distinct[value[:80]] += 1

    @property
    def looks_enumerated(self) -> bool:
        return 0 < len(self.distinct) <= 12 and self.max_len <= 40


@dataclass
class NodeProfile:
    """Everything observed at one element path, e.g. UNIFACE/UFORM/UPROC."""

    path: str
    count: int = 0
    with_text: int = 0
    text_min: int = 10 ** 9
    text_max: int = 0
    text_total: int = 0
    text_tags: Counter = field(default_factory=Counter)
    samples: list[str] = field(default_factory=list)
    attrs: "OrderedDict[str, AttrProfile]" = field(default_factory=OrderedDict)
    children: Counter = field(default_factory=Counter)
    max_siblings: int = 0

    @property
    def tag(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    @property
    def depth(self) -> int:
        return self.path.count("/")

    def observe_attrs(self, attrib: dict) -> None:
        for name, value in attrib.items():
            prof = self.attrs.get(name)
            if prof is None:
                prof = self.attrs[name] = AttrProfile(name)
            prof.observe(value)

    def observe_text(self, text) -> None:
        if not text or not text.strip():
            return
        self.with_text += 1
        n = len(text)
        self.text_min = min(self.text_min, n)
        self.text_max = max(self.text_max, n)
        self.text_total += n
        for tag in classify(text):
            self.text_tags[tag] += 1
        if len(self.samples) < MAX_SAMPLES:
            self.samples.append(text.strip()[:SAMPLE_CHARS])


@dataclass
class ProbeResult:
    source: str
    size_bytes: int
    declared_encoding: object
    detected_bom: object
    root: object
    nodes: "OrderedDict[str, NodeProfile]"
    notes: list = field(default_factory=list)


def sniff_encoding(raw: bytes):
    """Return (bom_name, declared_encoding) without committing to either."""
    boms = [
        ("utf-8-sig", b"\xef\xbb\xbf"),
        ("utf-32-le", b"\xff\xfe\x00\x00"),
        ("utf-32-be", b"\x00\x00\xfe\xff"),
        ("utf-16-le", b"\xff\xfe"),
        ("utf-16-be", b"\xfe\xff"),
    ]
    bom = next((name for name, sig in boms if raw.startswith(sig)), None)
    head = raw[:400]
    pattern = re.compile(r"encoding\s*=\s*[\"']([\w.\-]+)[\"']")
    for enc in ("utf-8", "utf-16-le", "utf-16-be", "latin-1"):
        try:
            text = head.decode(enc, errors="strict")
        except (UnicodeDecodeError, LookupError):
            continue
        match = pattern.search(text)
        if match:
            return bom, match.group(1)
        if "<?xml" in text or "<" in text:
            return bom, None
    return bom, None


def probe(path: Path) -> ProbeResult:
    raw = path.read_bytes()
    bom, declared = sniff_encoding(raw)
    nodes: "OrderedDict[str, NodeProfile]" = OrderedDict()
    notes: list = []
    root_tag = None

    lead = raw.lstrip()
    if lead[:1] not in (b"<", b""):
        notes.append(
            "File does not start with '<'. This is probably not XML -- it may be "
            "a legacy TRX export or a repository-table copy. First 120 bytes: "
            + repr(raw[:120])
        )
        return ProbeResult(str(path), len(raw), declared, bom, None, nodes, notes)

    stack: list = []
    # Track how many children of each tag a parent accumulates, so we can report
    # true cardinality (is UPROC one-per-component, or many?).
    sibling_counts: list = [Counter()]

    try:
        for event, elem in ET.iterparse(io.BytesIO(raw), events=("start", "end")):
            tag = elem.tag
            if event == "start":
                stack.append(tag)
                if root_tag is None:
                    root_tag = tag
                sibling_counts[-1][tag] += 1
                sibling_counts.append(Counter())
                key = "/".join(stack)
                node = nodes.get(key)
                if node is None:
                    node = nodes[key] = NodeProfile(key)
                node.count += 1
                node.observe_attrs(elem.attrib)
            else:
                key = "/".join(stack)
                node = nodes[key]
                node.observe_text(elem.text)
                own_children = sibling_counts.pop()
                for child, n in own_children.items():
                    node.children[child] += n
                    child_node = nodes.get(key + "/" + child)
                    if child_node is not None:
                        child_node.max_siblings = max(child_node.max_siblings, n)
                stack.pop()
                elem.clear()
    except ET.ParseError as exc:
        notes.append(
            "XML parse error at %s: %s. Report covers the prefix parsed so far."
            % (exc.position, exc)
        )

    return ProbeResult(str(path), len(raw), declared, bom, root_tag, nodes, notes)


def render(result: ProbeResult, show_samples: bool = True) -> str:
    out = io.StringIO()
    w = out.write
    w("source            %s\n" % result.source)
    w("size              {:,} bytes\n".format(result.size_bytes))
    w("bom               %s\n" % (result.detected_bom or "(none)"))
    w("declared encoding %s\n" % (result.declared_encoding or "(none declared)"))
    w("root element      %s\n" % (result.root or "(none)"))
    w("distinct paths    %d\n" % len(result.nodes))
    for note in result.notes:
        w("\n! %s\n" % note)
    if not result.nodes:
        return out.getvalue()

    w("\n" + "=" * 78 + "\nELEMENT TREE\n" + "=" * 78 + "\n")
    for node in result.nodes.values():
        indent = "  " * node.depth
        card = "x%d" % node.max_siblings if node.max_siblings > 1 else ""
        w("%s<%s>  n=%d %s\n" % (indent, node.tag, node.count, card))
        for attr in node.attrs.values():
            req = "required" if attr.count == node.count else "%d/%d" % (attr.count, node.count)
            line = "%s  @%-24s %s, len %d-%d" % (
                indent, attr.name, req, attr.min_len, attr.max_len,
            )
            if attr.looks_enumerated:
                vals = ", ".join(sorted(v for v, _ in attr.distinct.most_common(12)))
                line += ", enum{%s}" % vals
            w(line + "\n")
        if node.with_text:
            tags = ", ".join("%s(%d)" % (t, n) for t, n in node.text_tags.most_common())
            avg = node.text_total // max(node.with_text, 1)
            w("%s  #text  %d/%d non-empty, len %d-%d avg %d  [%s]\n" % (
                indent, node.with_text, node.count,
                node.text_min, node.text_max, avg, tags,
            ))

    if show_samples:
        interesting = [
            n for n in result.nodes.values()
            if n.with_text and (
                "procscript?" in n.text_tags
                or "base64" in n.text_tags
                or "multiline" in n.text_tags
                or n.text_max > 200
            )
        ]
        if interesting:
            w("\n" + "=" * 78 + "\nCONTENT SAMPLES (candidates for extraction)\n" + "=" * 78 + "\n")
            for node in interesting:
                w("\n--- %s  [%s]\n" % (node.path, ", ".join(node.text_tags)))
                for sample in node.samples[:2]:
                    for line in sample.splitlines() or [""]:
                        w("    | %s\n" % line)
                    w("    |\n")
    return out.getvalue()


def to_json(result: ProbeResult) -> str:
    def node_dict(n: NodeProfile) -> dict:
        return {
            "path": n.path,
            "count": n.count,
            "max_siblings": n.max_siblings,
            "attrs": {
                a.name: {
                    "count": a.count,
                    "min_len": a.min_len if a.count else 0,
                    "max_len": a.max_len,
                    "enumerated": a.looks_enumerated,
                    "values": [v for v, _ in a.distinct.most_common(12)] if a.looks_enumerated else [],
                }
                for a in n.attrs.values()
            },
            "text": {
                "non_empty": n.with_text,
                "min_len": n.text_min if n.with_text else 0,
                "max_len": n.text_max,
                "tags": dict(n.text_tags),
                "samples": n.samples,
            },
            "children": dict(n.children),
        }

    payload: dict[str, Any] = {
        "source": result.source,
        "size_bytes": result.size_bytes,
        "declared_encoding": result.declared_encoding,
        "bom": result.detected_bom,
        "root": result.root,
        "notes": result.notes,
        "nodes": [node_dict(n) for n in result.nodes.values()],
    }
    return json.dumps(payload, indent=2)
