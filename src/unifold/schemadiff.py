"""Compare the schemas of two exports -- typically 9.7 against 10.4.

Supporting both versions is a mapping problem, not two separate tools: the
element vocabulary differs, the underlying repository concepts largely do not.
This module diffs two probed exports so the per-version mappings can be written
against measured evidence rather than assumption.

Note the asymmetry this is designed to survive: if the two dialects share almost
no element names, the diff will show near-total disjointness. That is a finding,
not a failure -- it tells us the mapping must be built concept-by-concept rather
than by path correspondence.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

from .probe import ProbeResult


@dataclass
class SchemaDiff:
    left_label: str
    right_label: str
    only_left: list = field(default_factory=list)
    only_right: list = field(default_factory=list)
    shared: list = field(default_factory=list)
    # path -> (attrs only in left, attrs only in right)
    attr_deltas: dict = field(default_factory=dict)
    left_vocab: set = field(default_factory=set)
    right_vocab: set = field(default_factory=set)

    @property
    def vocab_overlap(self) -> float:
        """Fraction of element *names* (ignoring nesting) common to both."""
        union = self.left_vocab | self.right_vocab
        if not union:
            return 0.0
        return len(self.left_vocab & self.right_vocab) / len(union)

    @property
    def verdict(self) -> str:
        overlap = self.vocab_overlap
        if not self.only_left and not self.only_right and not self.attr_deltas:
            return "schemas are identical -- one mapping covers both versions"
        if overlap >= 0.8:
            return (
                "largely the same vocabulary with local differences -- one mapping "
                "plus per-version overrides should cover both"
            )
        if overlap >= 0.3:
            return (
                "partial vocabulary overlap -- shared concepts are recognisable, "
                "but each version needs its own mapping onto the neutral model"
            )
        return (
            "little or no shared vocabulary -- the two dialects must be mapped "
            "independently, concept by concept, onto the neutral model"
        )


def diff(left: ProbeResult, right: ProbeResult,
         left_label: str = "left", right_label: str = "right") -> SchemaDiff:
    lpaths = set(left.nodes)
    rpaths = set(right.nodes)

    result = SchemaDiff(
        left_label=left_label,
        right_label=right_label,
        only_left=sorted(lpaths - rpaths),
        only_right=sorted(rpaths - lpaths),
        shared=sorted(lpaths & rpaths),
        left_vocab={n.tag for n in left.nodes.values()},
        right_vocab={n.tag for n in right.nodes.values()},
    )

    for path in result.shared:
        lattrs = set(left.nodes[path].attrs)
        rattrs = set(right.nodes[path].attrs)
        if lattrs != rattrs:
            result.attr_deltas[path] = (sorted(lattrs - rattrs), sorted(rattrs - lattrs))

    return result


def render(d: SchemaDiff) -> str:
    out = io.StringIO()
    w = out.write
    w("left   %s\n" % d.left_label)
    w("right  %s\n" % d.right_label)
    w("\nshared paths        %d\n" % len(d.shared))
    w("only in left        %d\n" % len(d.only_left))
    w("only in right       %d\n" % len(d.only_right))
    w("element-name overlap %.0f%%\n" % (d.vocab_overlap * 100))
    w("\nVERDICT: %s\n" % d.verdict)

    if d.only_left:
        w("\n" + "=" * 78 + "\nONLY IN %s\n" % d.left_label.upper() + "=" * 78 + "\n")
        for path in d.only_left:
            w("  %s\n" % path)
    if d.only_right:
        w("\n" + "=" * 78 + "\nONLY IN %s\n" % d.right_label.upper() + "=" * 78 + "\n")
        for path in d.only_right:
            w("  %s\n" % path)
    if d.attr_deltas:
        w("\n" + "=" * 78 + "\nATTRIBUTE DIFFERENCES ON SHARED PATHS\n" + "=" * 78 + "\n")
        for path, (lonly, ronly) in sorted(d.attr_deltas.items()):
            w("  %s\n" % path)
            if lonly:
                w("    only left : %s\n" % ", ".join(lonly))
            if ronly:
                w("    only right: %s\n" % ", ".join(ronly))

    names_only_left = sorted(d.left_vocab - d.right_vocab)
    names_only_right = sorted(d.right_vocab - d.left_vocab)
    if names_only_left or names_only_right:
        w("\n" + "=" * 78 + "\nELEMENT NAMES (nesting ignored)\n" + "=" * 78 + "\n")
        w("  shared     : %s\n" % (", ".join(sorted(d.left_vocab & d.right_vocab)) or "(none)"))
        w("  only left  : %s\n" % (", ".join(names_only_left) or "(none)"))
        w("  only right : %s\n" % (", ".join(names_only_right) or "(none)"))
    return out.getvalue()
