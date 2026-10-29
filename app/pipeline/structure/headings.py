"""Heading levels: which blocks are headings, and the level each takes."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Callable

from app.pipeline.lists import label_kind
from app.pipeline.models import DocumentAnalysis, TextBlock, append_block, bbox_union
from app.pipeline.structure.formulas import mostly_math
from app.pipeline.markers import BULLET, LIST_LABEL


_SECTION_LABEL = re.compile(r"^\s*(part|chapter|section|unit|lesson|module)\s+(\d{1,3}|[ivxlc]{1,6}|[a-z])\s*[:.]\s", re.I)
# (a space typed after a dot inside the number, "2. 5.4 Plasma", is still the number 2.5.4, #665; "2. 45 gtt/min",
# an answer, is not, nor is "14. 5.56 mcg/kg/min": the number has to go on with another dot, then a capital)
_SECTION_NO = re.compile(r"^\s*(\d{1,3}(?:\.\s(?=\d{1,3}(?:\.\d{1,3})+\s+[A-Z]))?(?:\.?\d{1,3}){0,5})\.?\s+\S")
# A heading led by a section's label and number ("NOTE 1:", "Part A:"), for ranking it above unlabelled ones of its
# style. Not a caption's ("Table 1.", "Example 3:"): those head nothing below them
_LABELLED = re.compile(r"^\s*(note|part|chapter|section|article|appendix|unit|lesson|module)\s+"
                       r"(\d{1,3}|[IVXLC]{1,6}|[A-Z])\s*[:.]\s", re.I)
_PATTERN_NUM = re.compile(r"\d+(?:\.\d+)*|\b[ivxlc]+\b|\b[A-Z]\b", re.I)


_CHAPTER_NO = re.compile(r"^\s*(\d{1,2}|[IVX]{1,5})\s*$")


def join_chapter_numbers(doc: DocumentAnalysis) -> None:
    """A chapter's number set huge beside or above its title (a "1" in 80 points by "Introduction" in 28) is part of
    the title: read apart it was a paragraph of its own saying "1", and the title alone carried no number, so chapters
    whose titles happened to be set in different sizes got different levels. Joined ("1 Introduction"), the number
    says which chapter it is, and section numbers put every chapter at one level (#665)."""
    body = doc.body_font_size
    for p in doc.pages:
        for num in [b for b in p.blocks if b.role != "Artifact" and _CHAPTER_NO.match(b.text)
                    and b.font_size >= 2.5 * body and b.bbox[1] < 0.4 * p.height]:
            near = [b for b in p.blocks if b is not num and b.role != "Artifact" and b.font_size >= 1.5 * body
                    and not _CHAPTER_NO.match(b.text) and b.font_size < num.font_size
                    and b.bbox[1] < num.bbox[3] + 60 and b.bbox[3] > num.bbox[1] - 60]
            if not near:
                continue
            title = min(near, key=lambda b: abs((b.bbox[1] + b.bbox[3]) - (num.bbox[1] + num.bbox[3])))
            n = num.text.strip()
            title.text = f"{n} {title.text}"
            if title.actual_text:
                title.actual_text = f"{n} {title.actual_text}"
            title.bbox = bbox_union([title.bbox, num.bbox])
            p.blocks.remove(num)


# A page saying it's blank, or one closing a part ("END OF QUESTION"), opens nothing
_NOT_A_DIVIDER = re.compile(r"\bblank\b|^\W*end\b", re.I)


def join_divider_pages(doc: DocumentAnalysis) -> None:
    """A page holding only a few short lines, close together, opens a section: an audit report's "SECTION I" over
    "FINANCIAL", alone on the page before the auditor's report. Its lines are one title, at the top level, whatever its
    print: set in body-size plain capitals, the five such pages of a 72-page report were paragraphs, and nothing marked
    where its sections begin. Not the first page (the cover, whose title is found by size), not a page saying it is
    blank or ending a part, not lines already in a heading's print (a dedication or a chapter title set large is ranked
    by its print, with the rest), and only in a long document: at least ten pages."""
    if doc.page_count < 10:
        return
    body = doc.body_font_size
    for p in doc.pages[1:]:
        if p.tables or any(not i.decorative for i in p.images):
            continue
        text = sorted((b for b in p.blocks if b.role != "Artifact" and b.text.strip()), key=lambda b: b.bbox[1])
        words = [len(b.text.split()) for b in text]
        if not text or len(text) > 3 or sum(words) > 15 or max(words) > 10:
            continue
        joined = " ".join(b.text for b in text)
        if (sum(ch.isalpha() for ch in joined) < 3 or _NOT_A_DIVIDER.search(joined)
                or any(is_heading_candidate(b, body) for b in text)
                or any(b.text.rstrip().endswith((".", ",", ";")) for b in text)
                or text[-1].bbox[3] - text[0].bbox[1] > p.height / 3):
            continue
        title = text[0]
        for b in text[1:]:
            append_block(title, b)
            p.blocks.remove(b)
        title.divider = True


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
    def a_heading(b: TextBlock, blocks: list[TextBlock]) -> bool:
        if b.role in ("Artifact", "TOCI"):
            return False
        return b.divider or (is_heading_candidate(b, body) and not _within_a_question(blocks, b, body)
                             and (b.font_size >= body * 0.98 or _heads_its_text(blocks, b, body)))

    heads = [b for p in doc.pages for b in sorted(p.blocks, key=lambda b: (b.bbox[1], b.bbox[0]))
             if a_heading(b, p.blocks)]
    heads = _one_on_the_cover(doc, heads)
    if not heads:
        return
    # 1. size clusters, largest first
    cluster: dict[float, float] = {}
    anchor = None
    for size in sorted({b.font_size for b in heads}, reverse=True):
        if anchor is None or size < anchor * 0.9:
            anchor = size
        cluster[size] = anchor
    # (a section's title page outranks every heading in print)
    style = {id(b): (float("inf"), True) if b.divider else (cluster[b.font_size], b.bold) for b in heads}
    # 2. one style per wording pattern
    by_pattern: dict[str, list[TextBlock]] = {}
    for b in heads:
        # (a section number keeps its depth: "1 Introduction" is a chapter, "3.1 Introduction" a section in one)
        key = _PATTERN_NUM.sub(lambda m: re.sub(r"\d+", "#", m.group()) if m.group()[0].isdigit() else "#",
                               b.text.strip().lower())
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
    # A style whose headings are partly labelled ("NOTE 1: CASH AND INVESTMENTS") and partly not ("INVESTMENTS"): the
    # labelled ones open sections the others sit in, a half rank lower
    labelled_styles = Counter(style[id(b)] for b in heads if _LABELLED.match(b.text))
    for b in heads:
        if labelled_styles[style[id(b)]] >= 2 and not _LABELLED.match(b.text) and not b.divider:
            rank[id(b)] += 0.5
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
    page_only = _front_page_styles(doc, heads, rank)
    open_: list[float] = []
    page = None
    for b in heads:
        if b.page != page:
            open_ = [r for r in open_ if r not in page_only]
            page = b.page
        r = rank[id(b)]
        while open_ and open_[-1] >= r:
            open_.pop()
        open_.append(r)
        b.role = f"H{min(len(open_), 6)}"
    _fix_heading_skips(doc)


def _front_page_styles(doc: DocumentAnalysis, heads: list[TextBlock], rank: dict[int, float]) -> set[float]:
    """The ranks of headings on one early page only (after the cover: a certificate, a contents page), in print larger
    than any heading after that page: that page's own, closed at its end. Left open they held the whole document:
    a lab manual's 46-point "Certificate" put every experiment two levels down. The cover's title stays open: it is the
    document's."""
    if not doc.pages or doc.page_count < 10:
        return set()
    pages: dict[float, set[int]] = {}
    for b in heads:
        pages.setdefault(rank[id(b)], set()).add(b.page)
    out = set()
    for r, on in pages.items():
        (pg,) = on if len(on) == 1 else (None,)
        if pg is None or pg == doc.pages[0].number or pg >= 0.1 * doc.page_count:
            continue
        if all(rank[id(b)] > r for b in heads if b.page > pg):
            out.add(r)
    return out


def _one_on_the_cover(doc: DocumentAnalysis, heads: list[TextBlock]) -> list[TextBlock]:
    """A cover page (the first page of a long document, short lines only: the title, the author, the school, the
    degree) has one heading, its title: the largest line, bold before plain, the longest of those. The rest are in
    large print to be seen, not sections, and as headings they opened a thesis with seven of them (#665). A short
    document's first page, or one with a few large lines (a recipe's, a test bank's), is left as it was."""
    if not doc.pages or doc.page_count < 10:
        return heads
    first = doc.pages[0]
    text = [b for b in first.blocks if b.role != "Artifact" and b.text.strip()]
    on_cover = [b for b in heads if b.page == first.number]
    if len(on_cover) < 5 or any(len(b.text.split()) > 20 for b in text) or any(
            _SECTION_NO.match(b.text) or _SECTION_LABEL.match(b.text) for b in on_cover):
        return heads
    title = max(on_cover, key=lambda b: (round(b.font_size), b.bold, len(b.text.split())))
    return [b for b in heads if b.page != first.number or b is title]


def _within_a_question(blocks: list[TextBlock], b: TextBlock, body: float) -> bool:
    """A bold line at body size between a numbered item and the first of its lettered choices, indented under the
    item's label: the item's own second line ("28. Given the reaction shown, if the" then "standard potential of
    Mg?") or the header row of a small table it shows. As headings they opened sections inside the questions."""
    if b.font_size >= body * 1.15:
        return False
    size = max(b.font_size, 1.0)
    labelled = [(o, label_kind(m.group(1))) for o in blocks if o is not b and o.role != "Artifact"
                and (m := BULLET.match(o.text))]
    above = max(((o, k) for o, k in labelled if o.bbox[1] < b.bbox[1] and o.bbox[3] <= b.bbox[3]),
                key=lambda ok: ok[0].bbox[3], default=None)
    below = min(((o, k) for o, k in labelled if o.bbox[1] >= b.bbox[3] - 0.3 * size),
                key=lambda ok: (round(ok[0].bbox[1]), ok[0].bbox[0]), default=None)
    if above is None or below is None:
        return False
    (item, (key, _)), (choice, (sub, n)) = above, below
    if key.kind == "bullet" or sub.kind == "bullet" or sub == key or n != 1:
        return False
    if b.bbox[0] < item.bbox[0] + 0.8 * size or choice.bbox[1] - item.bbox[3] > 12 * size:
        return False
    # nothing in larger print between: a real heading would be
    return not any(o.font_size >= body * 1.15 and item.bbox[3] <= o.bbox[1] and o.bbox[3] <= choice.bbox[1]
                   for o in blocks if o.role != "Artifact")


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
    # Nor is a short one set in larger plain print ("Dedicated to my family."): a heading in a full stop is bold (#665)
    return (c.words == 0 or c.words > 25
            or c.b.text.endswith((".", ",", ";")) and (c.words > 8 or (not c.b.bold and c.words >= 3))
            and not _SECTION_LABEL.match(c.b.text) and not _SECTION_NO.match(c.b.text))


# A heading never stops on these: a line of plain print that does is a sentence carrying on below ("This page records the
# work carried out in the lab by", then the line to write the name on)
_RUNS_ON = frozenset("by of the a an and or to with from that".split())


def _runs_on(c: _Candidate) -> bool:
    words = c.b.text.split()
    return not c.b.bold and len(words) >= 6 and words[-1].lower() in _RUNS_ON


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


# Tesseract's mean confidence (0-100) below which an OCR'd block's size says nothing
UNSURE_OCR = 30.0


# Why a block isn't a heading, whatever its size, checked in this order; the first that holds rules it out
NOT_A_HEADING: list[tuple[str, Callable[[_Candidate], bool]]] = [
    # page furniture and OCR noise ("ee", "PT", "12")
    ("too little text", lambda c: sum(ch.isalpha() for ch in c.b.text) < 3),
    ("a sentence", _a_sentence),
    ("a sentence carrying on to the next line", _runs_on),
    ("a row of controls or decoration", _mostly_marks),
    # a contents entry, in the size of the heading it lists (#634)
    ("a contents entry", lambda c: bool(_LEADERS.search(c.b.text))),
    # a formula in larger print ("η2 = (t2ρ2)/(t1ρ1) × η1") is a Formula, not a heading
    ("a formula in larger print", lambda c: mostly_math(c.b)),
    # nor is a line with a stacked fraction in it, words and all ("Where n = W over M.W")
    ("a line with a stacked fraction", lambda c: c.b.fraction),
    ("a list item at body size", lambda c: bool(c.label) and c.b.font_size <= c.body * 1.05),
    ("a list item, even in larger print", _list_item_in_larger_print),
    # a scanned form's filled-in value, typed in larger print than the form's own (#653)
    ("a value typed onto a scanned form", lambda c: c.b.typed_on_scan),
    # a line OCR mostly failed to read: its box, and so its size, is as unsure as its letters (#653)
    ("read by OCR with little confidence", lambda c: c.b.ocr_conf is not None and c.b.ocr_conf < UNSURE_OCR),
]


# A section's number typed with a space after a dot ("2. 5.4 Reliability of results"): a number, not a list's "2."
SPACED_SECTION_NO = re.compile(r"^\s*\d{1,3}\.\s\d{1,3}(?:\.\d{1,3})+\.?\s+[A-Z]")


def is_heading_candidate(b: TextBlock, body: float) -> bool:
    label = None if SPACED_SECTION_NO.match(b.text) else BULLET.match(b.text)
    c = _Candidate(b, body, len(b.text.split()), [ch for ch in b.text if not ch.isspace()], label)
    if any(rule(c) for _, rule in NOT_A_HEADING):
        return False
    if b.font_size >= body * 1.15:
        return True
    if not b.bold or c.words > 12 or b.text.endswith("."):
        return False
    # A bold line in capitals can be a size smaller than the text and still head it (an audit report's notes: "NOTE 1:
    # DESCRIPTION OF ...", "REPORTING ENTITY" in 9.7-point bold over 10.6-point text)
    return b.font_size >= body * 0.98 or (b.font_size >= body * 0.85 and _capitals(b.text))


def _heads_its_text(blocks: list[TextBlock], b: TextBlock, body: float) -> bool:
    """A heading in print smaller than the text stands alone on its row, and the text it heads follows it: the next
    block below that isn't a heading too is plain text at the body's size, starting where the heading does. Bold
    capitals inside a chart ("OPIOID NAÏVE:" beside "Mild Pain") or over a table's columns are labels."""
    size = max(b.font_size, 1.0)
    if any(o is not b and o.role != "Artifact" and o.bbox[1] < b.bbox[3] and b.bbox[1] < o.bbox[3]
           for o in blocks):
        return False
    below = sorted((o for o in blocks if o.role != "Artifact" and o.bbox[1] >= b.bbox[3] - 0.3 * size
                    and o.bbox[0] < b.bbox[2] and b.bbox[0] < o.bbox[2]), key=lambda o: o.bbox[1])
    for o in below[:4]:
        if is_heading_candidate(o, body):
            continue
        return (not o.bold and abs(o.font_size - body) <= 0.1 * body and abs(o.bbox[0] - b.bbox[0]) <= 2 * size
                and o.bbox[1] - b.bbox[3] <= 4 * size)
    return False


def _capitals(text: str) -> bool:
    letters = [ch for ch in text if ch.isalpha()]
    return len(letters) >= 3 and sum(ch.isupper() for ch in letters) >= 0.8 * len(letters)


def _fix_heading_skips(doc: DocumentAnalysis) -> None:
    """PDF/UA best practice: heading levels must not skip (H1 -> H3). Levels are already consistent
    per style, so this only touches the rare heading that comes before any heading of the level above
    it (a document opening with a subheading)."""
    prev = 0
    for p in doc.pages:
        # (top to bottom, as the levels were given: a page's blocks can be listed in another order, a chapter's title
        # last, and its sections then looked like they skipped a level, #665)
        for b in sorted(p.blocks, key=lambda b: (b.bbox[1], b.bbox[0])):
            if not b.role.startswith("H"):
                continue
            level = int(b.role[1])
            if level > prev + 1:
                level = prev + 1
            b.role = f"H{level}"
            prev = level
