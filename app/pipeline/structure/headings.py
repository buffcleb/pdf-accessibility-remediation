"""Heading levels: which blocks are headings, and the level each takes."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Callable

from app.pipeline.models import DocumentAnalysis, TextBlock
from app.pipeline.structure.formulas import mostly_math
from app.pipeline.markers import BULLET, LIST_LABEL


_SECTION_LABEL = re.compile(r"^\s*(part|chapter|section|unit|lesson|module)\s+(\d{1,3}|[ivxlc]{1,6}|[a-z])\s*[:.]\s", re.I)
_SECTION_NO = re.compile(r"^\s*(\d{1,3}(?:\.\d{1,3}){0,5})\.?\s+\S")
_PATTERN_NUM = re.compile(r"\d+(?:\.\d+)*|\b[ivxlc]+\b|\b[A-Z]\b", re.I)


def assign_headings(doc: DocumentAnalysis) -> None:
    """Heading levels, the same for every heading that looks the same.

    1. Headings are grouped by style: font size (sizes within ~10% are one size; OCR'd and re-encoded
       text varies by a fraction of a point) and weight.
    2. Headings that share a wording pattern ("Worked Example 1", "Worked Example 4") but come in
       slightly different styles take the pattern's most common style.
    3. Larger styles rank higher; at the same size, the style that appears first in the document ranks
       higher (it opens the sections the other style sits in: "Worked Example", then its "Solution").
    4. Section numbers decide where present: "1.1" and "1.2" are one level, below "1" and above "1.1.1".
    5. Each heading's level is the number of higher-ranked headings open above it, so levels never skip
       and a style used only on a cover page doesn't push every later heading down.
    """
    body = doc.body_font_size
    heads = [b for p in doc.pages for b in sorted(p.blocks, key=lambda b: (b.bbox[1], b.bbox[0]))
             if b.role != "Artifact" and is_heading_candidate(b, body)]
    if not heads:
        return
    # 1. size clusters, largest first
    cluster: dict[float, float] = {}
    anchor = None
    for size in sorted({b.font_size for b in heads}, reverse=True):
        if anchor is None or size < anchor * 0.9:
            anchor = size
        cluster[size] = anchor
    style = {id(b): (cluster[b.font_size], b.bold) for b in heads}
    # 2. one style per wording pattern
    by_pattern: dict[str, list[TextBlock]] = {}
    for b in heads:
        key = _PATTERN_NUM.sub("#", b.text.strip().lower())
        if "#" in key and len(key) >= 4:
            by_pattern.setdefault(key, []).append(b)
    for group in by_pattern.values():
        styles = Counter(style[id(b)] for b in group)
        if len(group) >= 2 and len(styles) > 1:
            common, n = styles.most_common(1)[0]
            if n * 2 > len(group) or n >= 2:
                for b in group:
                    style[id(b)] = common
    # 3. rank styles: larger first; at the same size, the one seen first in the document
    first_seen: dict[tuple, int] = {}
    for n, b in enumerate(heads):
        first_seen.setdefault(style[id(b)], n)
    ranked = sorted(first_seen, key=lambda st: (-st[0], first_seen[st]))
    rank = {id(b): ranked.index(style[id(b)]) for b in heads}
    # 4. section numbers: one rank per numbering depth, deeper numbers ranked lower
    depth_ranks: dict[int, Counter] = {}
    for b in heads:
        m = _SECTION_NO.match(b.text)
        if m:
            depth_ranks.setdefault(m.group(1).count(".") + 1, Counter())[rank[id(b)]] += 1
    if sum(sum(c.values()) for c in depth_ranks.values()) >= 2:
        by_depth: dict[int, float] = {}
        prev = -1.0
        for d in sorted(depth_ranks):
            want = min(depth_ranks[d], key=lambda x: (-depth_ranks[d][x], x))
            by_depth[d] = max(float(want), prev + 0.5)
            prev = by_depth[d]
        for b in heads:
            m = _SECTION_NO.match(b.text)
            if m:
                rank[id(b)] = by_depth[m.group(1).count(".") + 1]
    # 5. level = how many higher-ranked headings are open above it. Headings of one style under the
    # same kind of parent get the same level, and no level is ever skipped.
    open_: list[float] = []
    for b in heads:
        r = rank[id(b)]
        while open_ and open_[-1] >= r:
            open_.pop()
        open_.append(r)
        b.role = f"H{min(len(open_), 6)}"
    _fix_heading_skips(doc)


@dataclass
class _Candidate:
    """A block being considered as a heading, with what the exclusions read from it worked out once."""
    b: TextBlock
    body: float
    words: int
    visible: list[str]
    label: re.Match | None


def _a_sentence(c: _Candidate) -> bool:
    # A sentence ending in a full stop isn't a heading, unless a section's label leads it ("Part 1: Some general
    # problems found on the exams.", #604)
    return (c.words == 0 or c.words > 25
            or c.b.text.endswith((".", ",", ";")) and c.words > 8 and not _SECTION_LABEL.match(c.b.text))


def _mostly_marks(c: _Candidate) -> bool:
    # A row of controls or decoration read as text (a screenshot's "[- 1 serving + |", a rule of dashes): its size
    # comes from the boxes and icons around it, not from being a heading (#438)
    marks = sum(1 for ch in c.visible if not ch.isalnum() and ch not in ".,:;'’\"“”()&/-–—?!%$#")
    return len(c.visible) >= 6 and marks >= 3 and marks >= 0.15 * len(c.visible)


def _list_item_in_larger_print(c: _Candidate) -> bool:
    # Items of a numbered or lettered list, even in larger print than the body ("1. If the pressure is decreased...
    # which way will it shift?" then "(a) H2 + O2"): a heading's number is a section's, a numbered heading is short,
    # and a heading is neither a question nor the lead-in to what follows it (#597)
    return bool(c.label) and (bool(LIST_LABEL.fullmatch(c.label.group(1))) or c.words > 12
                              or c.b.text.rstrip().endswith(("?", ":")))


# Dot leaders: words, then a line of dots to what ends the line, a page number or a word or two ("Introduction ........
# 2"). Dots before the words are an ornament, as a title can have
_LEADERS = re.compile(r"[^\W\d_]{2}.*?(?:[.\u00b7\u2026]\s*){4,}[^.\u00b7\u2026]{0,30}\.?\s*$")


# Why a block isn't a heading, whatever its size, checked in this order; the first that holds rules it out
NOT_A_HEADING: list[tuple[str, Callable[[_Candidate], bool]]] = [
    # page furniture and OCR noise ("ee", "PT", "12")
    ("too little text", lambda c: sum(ch.isalpha() for ch in c.b.text) < 3),
    ("a sentence", _a_sentence),
    ("a row of controls or decoration", _mostly_marks),
    # a contents entry, in the size of the heading it lists (#634)
    ("a contents entry", lambda c: bool(_LEADERS.search(c.b.text))),
    # a formula in larger print ("η2 = (t2ρ2)/(t1ρ1) × η1") is a Formula, not a heading
    ("a formula in larger print", lambda c: mostly_math(c.b)),
    ("a list item at body size", lambda c: bool(c.label) and c.b.font_size <= c.body * 1.05),
    ("a list item, even in larger print", _list_item_in_larger_print),
]


def is_heading_candidate(b: TextBlock, body: float) -> bool:
    c = _Candidate(b, body, len(b.text.split()), [ch for ch in b.text if not ch.isspace()], BULLET.match(b.text))
    if any(rule(c) for _, rule in NOT_A_HEADING):
        return False
    if b.font_size >= body * 1.15:
        return True
    return b.bold and b.font_size >= body * 0.98 and c.words <= 12 and not b.text.endswith(".")


def _fix_heading_skips(doc: DocumentAnalysis) -> None:
    """PDF/UA best practice: heading levels must not skip (H1 -> H3). Levels are already consistent
    per style, so this only touches the rare heading that comes before any heading of the level above
    it (a document opening with a subheading)."""
    prev = 0
    for p in doc.pages:
        for b in p.blocks:
            if not b.role.startswith("H"):
                continue
            level = int(b.role[1])
            if level > prev + 1:
                level = prev + 1
            b.role = f"H{level}"
            prev = level
