"""List items and captions, and lines that continue a list item."""
from __future__ import annotations

from app.pipeline.markers import BULLET, CAPTION, FIGURE_CAPTION, TABLE_CAPTION
from app.pipeline.models import DocumentAnalysis, ImageRegion, PageAnalysis, TableRegion, TextBlock, append_block


def assign_lists_and_captions(doc: DocumentAnalysis) -> None:
    for p in doc.pages:
        for b in p.blocks:
            if b.role != "P":
                continue
            m = BULLET.match(b.text)
            if m:
                b.role = "LI"
                b.list_label = m.group(1)
            elif CAPTION.match(b.text) and len(b.text) < 300:
                b.role = "Caption"


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
                if indented and abs(b.font_size - li.font_size) <= max(0.6, 0.15 * size):
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
        gap_max = max(24.0, 3.0 * (cap.font_size or 10.0))
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
