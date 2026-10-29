"""List items and captions, and lines that continue a list item."""
from __future__ import annotations

import re

from app.pipeline.markers import BULLET, CAPTION, FIGURE_CAPTION, TABLE_CAPTION
from app.pipeline.models import DocumentAnalysis, ImageRegion, PageAnalysis, TableRegion, TextBlock, append_block


def assign_lists_and_captions(doc: DocumentAnalysis) -> None:
    before = None  # the text block above, over pages too
    for p in doc.pages:
        for b in sorted(p.blocks, key=lambda b: (b.bbox[1], b.bbox[0])):
            if b.role == "Artifact":
                continue
            above, before = before, b
            if b.role != "P":
                continue
            m = BULLET.match(b.text)
            if m and _closes_a_bracket(m.group(1), above, b.text[m.end():]):
                continue  # "(10" at the foot of a page, "g) and solvent" at the top of the next: one sentence
            if m:
                b.role = "LI"
                b.list_label = m.group(1)
            elif CAPTION.match(b.text) and len(b.text) < 300:
                b.role = "Caption"
    _bare_numbered_runs(doc)


def _closes_a_bracket(label: str, above: TextBlock | None, rest: str) -> bool:
    """A label that only closes a bracket ("g)", "K)") where the text above ends in one left open ("... catalyst
    (10"), and a word after it goes on in lower case ("and", not a variable's "zn"): the sentence goes on from there,
    broken over a page or a column, and is no list item (#665)."""
    words = above.text.split() if above is not None else []
    return (label.endswith(")") and "(" not in label and bool(words) and words[-1].startswith("(")
            and ")" not in words[-1] and bool(re.match(r"\s*[a-z]{3,}\b", rest)))


# A number with no full stop or bracket after it, then a word: "1 Planning - A careful plan saves time later"
_BARE_NUMBER = re.compile(r"^\s*(\d{1,2})\s+[A-Z\u00c0-\u00de]")


def _bare_numbered_runs(doc: DocumentAnalysis) -> None:
    """Paragraphs numbered 1, 2, 3 ... with the number alone (no full stop, as "The 12 principles" are set), one under
    another at one left edge, over pages too: a numbered list. One such paragraph alone is a sentence that starts with
    a number ("3 samples were taken"), so it takes three in a row, counting up from 1 (#665)."""
    run: list[TextBlock] = []

    def close() -> None:
        if len(run) >= 3:
            for b in run:
                b.role = "LI"
                b.list_label = _BARE_NUMBER.match(b.text).group(1)
        run.clear()

    for p in doc.pages:
        for b in sorted(p.blocks, key=lambda b: (b.bbox[1], b.bbox[0])):
            if b.role == "Artifact":
                continue
            m = _BARE_NUMBER.match(b.text) if b.role == "P" else None
            n = int(m.group(1)) if m else None
            if n is not None and run and n == int(_BARE_NUMBER.match(run[-1].text).group(1)) + 1 \
                    and abs(b.bbox[0] - run[0].bbox[0]) <= 3:
                run.append(b)
                continue
            if b.role == "P" and run and n is None and b.text[:1].islower():
                continue  # the item above, its last line set apart
            close()
            if n == 1:
                run.append(b)
    close()


def attach_list_continuations(doc: DocumentAnalysis) -> None:
    """A line indented under a list item and directly below it continues that item ("(a) 2710 = 2.71 ×
    1000" then "= 2.71 × 10³"). Joined to the item, the items stay one list instead of a run of one-item
    lists split by stray paragraphs."""
    for p in doc.pages:
        blocks = sorted(p.blocks, key=lambda b: (b.bbox[1], b.bbox[0]))
        absorbed: set[int] = set()
        for li in blocks:
            if li.role != "LI" or id(li) in absorbed:
                continue
            size = max(li.font_size, 1.0)
            for b in blocks:
                if id(b) in absorbed or b is li or b.role != "P" or BULLET.match(b.text):
                    continue
                gap = b.bbox[1] - li.bbox[3]
                if not -0.3 * size <= gap <= 0.9 * size:
                    continue
                indented = li.bbox[0] + 0.8 * size <= b.bbox[0] < li.bbox[2]
                # (or at the item's own edge, going on in lower case: an item set flush, its last line in another
                # weight than the rest, #665)
                flush = abs(b.bbox[0] - li.bbox[0]) <= 3 and b.text[:1].islower()
                if (indented or flush) and abs(b.font_size - li.font_size) <= max(0.6, 0.15 * size):
                    append_block(li, b)
                    absorbed.add(id(b))
        if absorbed:
            p.blocks = [b for b in p.blocks if id(b) not in absorbed]


def caption_pairs(page: PageAnalysis) -> dict[str, str]:
    """Caption block id -> the figure or table it describes. A caption pairs only with its neighbor in reading
    order (so pairing never reorders anything), of the matching kind ("Table 2" with a table, "Figure 1" or
    "Chart 3" with a figure; "Exhibit" with either), sitting just above or below it with their columns
    overlapping. Anything less clear stays a paragraph."""
    order = page.reading_order
    pairs: dict[str, str] = {}
    taken: set[str] = set()
    for i, eid in enumerate(order):
        cap = page.element(eid)
        if not isinstance(cap, TextBlock) or cap.role != "Caption":
            continue
        want_table = bool(TABLE_CAPTION.match(cap.text))
        want_figure = bool(FIGURE_CAPTION.match(cap.text))
        # (up to four lines' height: Word sets a blank line between a figure and its caption, #665)
        gap_max = max(24.0, 4.0 * (cap.font_size or 10.0))
        for j in (i - 1, i + 1):  # the element just before (caption below it) or just after (caption above it)
            if not 0 <= j < len(order) or order[j] in taken:
                continue
            other = page.element(order[j])
            if isinstance(other, ImageRegion):
                if other.decorative or other.kind == "formula" or want_table:
                    continue
            elif isinstance(other, TableRegion):
                if want_figure:
                    continue
            else:
                continue
            below = cap.bbox[1] >= other.bbox[1]
            gap = cap.bbox[1] - other.bbox[3] if below else other.bbox[1] - cap.bbox[3]
            overlap = min(cap.bbox[2], other.bbox[2]) - max(cap.bbox[0], other.bbox[0])
            if -2.0 <= gap <= gap_max and overlap > 0.3 * min(cap.bbox[2] - cap.bbox[0], other.bbox[2] - other.bbox[0]):
                pairs[cap.id] = other.id
                taken.add(other.id)
                break
    return pairs
