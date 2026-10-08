"""Heuristic logical structure: headings, lists, running headers/footers, columns, reading order."""
from __future__ import annotations

import re
from collections import Counter

from app.pipeline.models import BBox, DocumentAnalysis, PageAnalysis, TextBlock, append_block

BULLET_CHARS = "•‣◦⁃∙▪●■·➢✓✔"
_BULLET = re.compile(r"^\s*([" + BULLET_CHARS + r"\-\*–]|\(?\d{1,3}[.)]|\(?[a-zA-Z][.)]|\(?[ivxlcIVXLC]{1,5}[.)])\s+")
BULLET_ONLY = re.compile(r"^\s*[" + BULLET_CHARS + r"\-\*–]\s*$")
BULLET_START = _BULLET
_PAGE_NUM = re.compile(r"^\s*(page\s*)?\d{1,4}(\s*(of|/)\s*\d{1,4})?\s*$", re.I)
_DIGITS = re.compile(r"\d+")
_CAPTION = re.compile(r"^\s*(figure|fig\.|table|chart|exhibit|image)\s*\d", re.I)


def assign_roles(doc: DocumentAnalysis) -> None:
    _mark_running_artifacts(doc)
    _mark_repeated_images(doc)
    _assign_headings(doc)
    _mark_heading_icons(doc)
    _assign_lists_and_captions(doc)
    _attach_list_continuations(doc)
    _assign_formulas(doc)


def _icon_shaped(bbox: BBox, page_width: float) -> bool:
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    return 12 <= min(w, h) and max(w, h) <= 0.1 * page_width and 0.5 <= w / max(h, 1e-6) <= 2.0


def _mark_repeated_images(doc: DocumentAnalysis) -> None:
    """Images used over and over are decoration, not content: a small icon repeated next to every
    "Worked Example" heading, or a logo in the same spot on most pages. They become artifacts (and need
    no alt text). A one-off image is never touched here."""
    uses: dict[int, list] = {}
    for p in doc.pages:
        for img in p.images:
            if img.xref > 0:
                uses.setdefault(img.xref, []).append((p, img))
    for occurrences in uses.values():
        if len(occurrences) < 3:
            continue
        # An icon: small, roughly square and big enough to be a picture (word-sized slivers of text drawn as
        # images are not icons)
        small = all(_icon_shaped(i.bbox, p.width) for p, i in occurrences)
        first = occurrences[0][1].bbox
        same_spot = all(max(abs(a - b) for a, b in zip(i.bbox, first)) <= 5 for _, i in occurrences)
        on_most_pages = len({p.number for p, _ in occurrences}) >= max(2, 0.5 * doc.page_count)
        if small or (same_spot and on_most_pages):
            for _, img in occurrences:
                img.decorative = True
                img.alt = None


def _mark_heading_icons(doc: DocumentAnalysis) -> None:
    """A small picture just left of a heading ("Note", "Worked Example") marks the heading; the heading
    already says it in words, so the picture is decoration."""
    for p in doc.pages:
        heads = [b for b in p.blocks if b.role.startswith("H")]
        for img in p.images:
            w, h = img.bbox[2] - img.bbox[0], img.bbox[3] - img.bbox[1]
            if img.decorative or max(w, h) > 0.08 * p.width or not _icon_shaped(img.bbox, p.width):
                continue
            cy = (img.bbox[1] + img.bbox[3]) / 2
            for hd in heads:
                left_of = 0 <= hd.bbox[0] - img.bbox[2] <= 60
                level = hd.bbox[1] - h <= cy <= hd.bbox[3] + h
                if left_of and level:
                    img.decorative = True
                    img.alt = None
                    break


def _attach_list_continuations(doc: DocumentAnalysis) -> None:
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
                if id(b) in absorbed or b is li or b.role != "P" or _BULLET.match(b.text):
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


_MATH_CHARS = set("=×÷±≤≥<>+−√∑∫∞≈≠·^")
_WORD = re.compile(r"[A-Za-z]{3,}")


def _assign_formulas(doc: DocumentAnalysis) -> None:
    """A paragraph that is mostly math (operators, numbers, few words) is a Formula; its spoken form
    becomes the alt text. Prose that merely contains a little math stays a paragraph (with actual text
    for its powers)."""
    for p in doc.pages:
        for b in p.blocks:
            if b.role != "P":
                continue
            t = b.text
            dense = len(re.sub(r"\s", "", t)) or 1
            words = sum(len(w) for w in _WORD.findall(t))
            if any(c in _MATH_CHARS for c in t) and (b.has_scripts or any(c.isdigit() for c in t)) \
                    and words <= 0.25 * dense:
                b.role = "Formula"


def _mark_running_artifacts(doc: DocumentAnalysis) -> None:
    """Headers, footers and page numbers repeated across pages become Pagination artifacts."""
    band = 0.08
    counts: Counter = Counter()
    candidates: list[tuple[TextBlock, str]] = []
    for p in doc.pages:
        for b in p.blocks:
            top = b.bbox[3] <= p.height * band
            bottom = b.bbox[1] >= p.height * (1 - band)
            if not (top or bottom):
                continue
            norm = _DIGITS.sub("#", b.text.lower())
            candidates.append((b, norm))
            counts[norm] += 1
    threshold = max(2, int(doc.page_count * 0.5))
    for b, norm in candidates:
        if _PAGE_NUM.match(b.text) or (doc.page_count >= 2 and counts[norm] >= threshold):
            b.role = "Artifact"
            b.artifact_type = "Pagination"
    # "Page 3 of 58" in a short line near the top or bottom is page furniture even when it appears on
    # only a few pages (cover sheets added by a submission system)
    for p in doc.pages:
        for b in p.blocks:
            if b.role == "Artifact" or len(b.text) > 140 or not _PAGE_OF.search(b.text):
                continue
            if b.bbox[3] <= p.height * 0.15 or b.bbox[1] >= p.height * 0.85:
                b.role = "Artifact"
                b.artifact_type = "Pagination"


_PAGE_OF = re.compile(r"\bpage\s+\d{1,5}\s*(?:of|/)\s*\d{1,5}\b", re.I)
_SECTION_NO = re.compile(r"^\s*(\d{1,3}(?:\.\d{1,3}){0,5})\.?\s+\S")
_PATTERN_NUM = re.compile(r"\d+(?:\.\d+)*|\b[ivxlc]+\b|\b[A-Z]\b", re.I)


def _assign_headings(doc: DocumentAnalysis) -> None:
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
             if b.role != "Artifact" and _is_heading_candidate(b, body)]
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


def _is_heading_candidate(b: TextBlock, body: float) -> bool:
    if sum(ch.isalpha() for ch in b.text) < 3:
        return False  # page furniture and OCR noise ("ee", "PT", "12")
    words = len(b.text.split())
    if words == 0 or words > 25 or b.text.endswith((".", ",", ";")) and words > 8:
        return False
    if _BULLET.match(b.text) and b.font_size <= body * 1.05:
        return False
    if b.font_size >= body * 1.15:
        return True
    return b.bold and b.font_size >= body * 0.98 and words <= 12 and not b.text.endswith(".")


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


def _assign_lists_and_captions(doc: DocumentAnalysis) -> None:
    for p in doc.pages:
        for b in p.blocks:
            if b.role != "P":
                continue
            m = _BULLET.match(b.text)
            if m:
                b.role = "LI"
                b.list_label = m.group(1)
            elif _CAPTION.match(b.text) and len(b.text) < 300:
                b.role = "Caption"


# --------------------------------------------------------------------------- reading order
def row_top(b: TextBlock) -> float:
    """Where a block's first row of text starts: from its baseline, so a stacked fraction or exponent
    reaching above the line doesn't put the block ahead of its neighbors on the same row."""
    if b.baseline is None or not b.font_size:
        return b.bbox[1]
    return max(b.bbox[1], b.baseline - 0.8 * b.font_size)


def _elements(page: PageAnalysis) -> list[tuple[str, BBox]]:
    items: list[tuple[str, BBox]] = [(b.id, (b.bbox[0], row_top(b), b.bbox[2], b.bbox[3]))
                                     for b in page.blocks if b.role != "Artifact"]
    items += [(i.id, i.bbox) for i in page.images]
    items += [(t.id, t.bbox) for t in page.tables]
    return items


def detect_columns(page: PageAnalysis) -> int:
    text = [b.bbox for b in page.blocks if b.role != "Artifact" and len(b.text) > 40]
    if len(text) < 4:
        return 1
    return len(_gutters(text, page.width)) + 1


def _gutters(boxes: list[BBox], width: float) -> list[float]:
    """X positions (between 15% and 85% of the width) not crossed by any box."""
    if not boxes:
        return []
    step = 2.0
    lo, hi = width * 0.15, width * 0.85
    covered = []
    x = lo
    while x <= hi:
        covered.append((x, any(b[0] < x < b[2] for b in boxes)))
        x += step
    gutters, run = [], []
    for x, cov in covered:
        if not cov:
            run.append(x)
        elif run:
            if len(run) * step >= 8:
                gutters.append(sum(run) / len(run))
            run = []
    if run and len(run) * step >= 8:
        gutters.append(sum(run) / len(run))
    # A real column gutter has content side by side across it (vertically overlapping), not just
    # something above-left and something below-right of a gap, and on more than one row: a single row of
    # items spread across the page ("(i) 10 s   (ii) 20 s   (iii) 30 s") isn't two columns.
    def rows_across(g: float) -> int:
        spans = sorted((max(l[1], r[1]), min(l[3], r[3])) for l in boxes for r in boxes
                       if l[2] <= g <= r[0] and min(l[3], r[3]) - max(l[1], r[1]) > 0)
        rows, end = 0, float("-inf")
        for top, bottom in spans:
            if top >= end:
                rows += 1
                end = bottom
            else:
                end = max(end, bottom)
        return rows
    return [g for g in gutters if rows_across(g) >= 2]


def _by_rows(items: list[tuple[str, BBox]]) -> list[tuple[str, BBox]]:
    """Top to bottom, and left to right within a row. Items whose tops are within a few points of the
    row's first item share the row: sorting on the exact top would let a point's difference put the
    right-hand item of a row first."""
    rest = sorted(items, key=lambda it: (it[1][1], it[1][0]))
    out: list[tuple[str, BBox]] = []
    while rest:
        first = rest[0]
        h = max(1.0, first[1][3] - first[1][1])
        tol = min(3.0, 0.35 * h)
        row = [it for it in rest if it[1][1] - first[1][1] <= tol]
        out += sorted(row, key=lambda it: it[1][0])
        rest = [it for it in rest if it not in row]
    return out


def heuristic_reading_order(page: PageAnalysis) -> list[str]:
    """Top-to-bottom bands separated by full-width elements; columns left-to-right inside a band."""
    items = sorted(_elements(page), key=lambda it: (round(it[1][1], 0), it[1][0]))
    if not items:
        return []
    full = page.width * 0.6
    order: list[str] = []
    band: list[tuple[str, BBox]] = []

    def flush() -> None:
        if not band:
            return
        gutters = _gutters([b for _, b in band], page.width)
        if not gutters:
            order.extend(i for i, _ in _by_rows(band))
        else:
            edges = [float("-inf"), *gutters, float("inf")]
            for lo, hi in zip(edges, edges[1:]):
                col = [it for it in band if lo <= (it[1][0] + it[1][2]) / 2 < hi]
                order.extend(i for i, _ in _by_rows(col))
        band.clear()

    for it in items:
        if it[1][2] - it[1][0] >= full:
            flush()
            order.append(it[0])
        else:
            band.append(it)
    flush()
    return order


def needs_ai_reading_order(page: PageAnalysis) -> bool:
    return page.columns > 1 or (len(page.images) + len(page.tables) > 0 and len(page.blocks) > 6)
