"""Heuristic logical structure: headings, lists, running headers/footers, columns, reading order."""
from __future__ import annotations

import bisect

import re
from collections import Counter

from app.pipeline.models import (BBox, DocumentAnalysis, ImageRegion, PageAnalysis, TableRegion, TextBlock,
                                 append_block)

BULLET_CHARS = "•‣◦⁃∙▪●■·➢✓✔"
_BULLET = re.compile(r"^\s*([" + BULLET_CHARS + r"\-\*–]|\(?\d{1,3}[.)]|\(?[a-zA-Z][.)]|\(?[ivxlcIVXLC]{1,5}[.)])\s+")
BULLET_ONLY = re.compile(r"^\s*[" + BULLET_CHARS + r"\-\*–]\s*$")
BULLET_START = _BULLET
_PAGE_NUM = re.compile(r"^\s*(page\s*)?\d{1,4}(\s*(of|/)\s*\d{1,4})?\s*$", re.I)
_DIGITS = re.compile(r"\d+")
_CAPTION = re.compile(r"^\s*(figure|fig\.|table|chart|exhibit|image)\s*\d", re.I)
_TABLE_CAPTION = re.compile(r"^\s*table\b", re.I)
# What a footnote mark looks like: a number of up to three digits, or up to three of the usual symbols
FOOTNOTE_MARK = re.compile(r"^(\d{1,3}|[*†‡§¶]{1,3})$")
# A footnote's own number or symbol, written at its start ("1 Smith", "2. See", "* Rates")
_FOOTNOTE_LEAD = re.compile(r"^\s*(\d{1,3}|[*†‡§¶]{1,3})(?:[.)]\s*|\s+)(?=\S)")
_FIGURE_CAPTION = re.compile(r"^\s*(figure|fig\.|chart|image)", re.I)


def assign_roles(doc: DocumentAnalysis) -> None:
    _mark_running_artifacts(doc)
    _mark_repeated_images(doc)
    _assign_headings(doc)
    _mark_heading_icons(doc)
    _mark_text_badges(doc)
    _assign_lists_and_captions(doc)
    _attach_list_continuations(doc)
    _assign_formulas(doc)
    _mark_footnotes(doc)
    _mark_table_notes(doc)


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


def _mark_text_badges(doc: DocumentAnalysis) -> None:
    """A small shape with a short label printed inside it (a number in a circle, a letter on a tab, as slides draw
    them) is the label's background: the label says what it means, so the shape, and any shadow drawn under it as
    another image, is decoration. Otherwise each copy gets its own description ("a purple circle") while the number
    is read anyway."""
    for p in doc.pages:
        # Short labels, alone or as a row of them read as one line ("1 2 3 4 5": one badge each)
        labels = [b for b in p.blocks if b.role != "Artifact" and b.text.split()
                  and len(b.text.split()) <= 8 and all(len(t) <= 4 for t in b.text.split())]
        for img in p.images:
            # A figure made of pieces (a small molecule with its atoms written on it) is a picture with labels,
            # not a shape behind one
            if img.decorative or img.holds_pieces:
                continue
            w, h = img.bbox[2] - img.bbox[0], img.bbox[3] - img.bbox[1]
            if min(w, h) < 8 or max(w, h) > 0.12 * p.width:
                continue
            x0, y0, x1, y1 = img.bbox
            cx = (x0 + x1) / 2
            for b in labels:
                within_height = y0 - 2 <= b.bbox[1] and b.bbox[3] <= y1 + 2
                alone_inside = x0 - 2 <= b.bbox[0] and b.bbox[2] <= x1 + 2
                row_across = len(b.text.split()) > 1 and b.bbox[0] <= cx <= b.bbox[2]
                if within_height and (alone_inside or row_across):
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
    # A row of controls or decoration read as text (a screenshot's "[- 1 serving + |", a rule of dashes): its
    # size comes from the boxes and icons around it, not from being a heading
    visible = [ch for ch in b.text if not ch.isspace()]
    marks = sum(1 for ch in visible if not ch.isalnum() and ch not in ".,:;'’\"“”()&/-–—?!%$#")
    if len(visible) >= 6 and marks >= 3 and marks >= 0.15 * len(visible):
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


def _mark_footnotes(doc: DocumentAnalysis) -> None:
    """Footnotes: small print in the lower part of a page that starts with a mark (a number or *, †, ...) raised
    after a word in the larger text above it on the same page. The mark is the evidence: small numbered lines at
    the foot of a page are just as often a table's values, list items or captions, and those have no raised mark
    pointing at them. A footnote becomes a Note; the blocks whose marks point at it remember it (see notes)."""
    body = doc.body_font_size
    if not body:
        return
    for p in doc.pages:
        pointing = [b for b in p.blocks if b.marks and b.role != "Artifact"]
        if not pointing:
            continue
        marks = {mk for r in pointing for mk in r.marks}
        for b in list(p.blocks):
            if b.role not in ("P", "LI") or b.font_size > 0.9 * body or b.bbox[1] < 0.55 * p.height:
                continue
            m = _FOOTNOTE_LEAD.match(b.text)
            lead = b.lead_mark or (m.group(1) if m else None)
            alone = None if lead else _lone_mark(p, b, marks)
            if alone is not None:
                lead = alone.text.strip()
            words = b.text.split()
            letters = sum(ch.isalpha() for ch in b.text) / max(1, len(b.text.replace(" ", "")))
            if not lead or len(words) < 4 or letters < 0.5:
                continue  # a value, a lone number or a row of figures, not a note
            src = [r for r in pointing if r is not b and lead in r.marks and r.font_size > b.font_size
                   and r.bbox[3] <= b.bbox[1] + 1]
            if not src:
                continue
            if alone is not None:  # the note's number, set apart by a gap, is part of the note
                b.text = f"{lead} {b.text}"
                if b.actual_text:
                    b.actual_text = f"{lead} {b.actual_text}"
                b.bbox = (min(b.bbox[0], alone.bbox[0]), min(b.bbox[1], alone.bbox[1]), max(b.bbox[2], alone.bbox[2]),
                          max(b.bbox[3], alone.bbox[3]))
                p.blocks.remove(alone)
                if alone.id in p.reading_order:
                    p.reading_order.remove(alone.id)
            b.role, b.list_label, b.note_mark = "Note", None, lead
            for r in src:
                if b.id not in r.notes:
                    r.notes.append(b.id)
        notes = {b.id: b.note_mark for b in p.blocks if b.role == "Note" and b.note_mark}
        for r in pointing:
            by_mark = {notes[n]: n for n in r.notes if n in notes}
            r.mark_notes = [by_mark.get(mk) for mk in r.marks]
            _say_footnote_marks(r, set(by_mark))


# How a footnote symbol is said
_MARK_NAMES = {"*": "asterisk", "†": "dagger", "‡": "double dagger", "§": "section mark", "¶": "paragraph mark"}


def footnote_words(mark: str) -> str:
    """How a footnote mark is said: "footnote 1", "footnote asterisk"."""
    return "footnote " + (mark if mark.isdigit() else " ".join(_MARK_NAMES.get(c, c) for c in mark))


def _say_footnote_marks(b: TextBlock, noted: set[str]) -> None:
    """A mark written onto a word ("soils¹") is read as part of it ("soils1"). Where the mark has a footnote on the
    page, the read-aloud text says it as one: "soils (footnote 1)". Only the exact word and mark the analyzer saw
    raised change, in order, so an "R1" elsewhere in the paragraph stays as it is."""
    spoken = b.actual_text or b.text
    pos, changed = 0, False
    for mark, before in zip(b.marks, b.mark_after):
        if mark not in noted or not before.strip():
            continue
        glued = before + mark
        at = spoken.find(glued, pos)
        if at < 0:
            at = spoken.find(glued)  # the read-aloud order can differ from the drawing order
        if at < 0:
            continue
        said = f"{before} ({footnote_words(mark)})"
        spoken = spoken[:at] + said + spoken[at + len(glued):]
        pos, changed = at + len(said), True
    if changed:
        b.says_marks_only = b.actual_text is None  # nothing else needed saying: the tags can say the marks instead
        b.actual_text = spoken


# A table note's mark: one of the usual symbols, up to three of them
_TABLE_MARK = re.compile(r"[*†‡§¶]{1,3}")
_TABLE_NOTE = re.compile(r"^\s*([*†‡§¶]{1,3})\s*(?=\S)")
_TABLE_MARK_NAMES = {"*": "asterisk", "†": "dagger", "‡": "double dagger", "§": "section mark", "¶": "paragraph mark"}


def _mark_table_notes(doc: DocumentAnalysis) -> None:
    """Notes under a table: a block right below it (within about 120 points, across its width) that starts with a
    symbol (*, †, ...) its cells carry. Measured on a 332-page clinical guideline: such notes sit right under their
    table, and fewer than half are in smaller print, so the symbol in the cells is the evidence, not the size. Letters
    (a, b) aren't taken: a cell's "a" is as likely a word. The note becomes a Note; each cell with the symbol says
    it as a footnote ("120 (footnote asterisk)") and, once tagged, points at the note (struct_tree)."""
    for p in doc.pages:
        for t in p.tables:
            carried = {m for c in t.cells for m in _TABLE_MARK.findall(c.text or "")}
            if not carried:
                continue
            for b in p.blocks:
                m = _TABLE_NOTE.match(b.text or "")
                if (not m or m.group(1) not in carried or m.group(1) in t.notes or b.role not in ("P", "LI")
                        or len(b.text.split()) < 2 or not (t.bbox[3] - 2 <= b.bbox[1] <= t.bbox[3] + 120)
                        or b.bbox[2] < t.bbox[0] or b.bbox[0] > t.bbox[2]):
                    continue
                b.role, b.list_label, b.note_mark = "Note", None, m.group(1)
                t.notes[m.group(1)] = b.id
            for c in t.cells:
                said = _say_table_marks(c.actual_text or c.text or "", t.notes)
                if said != (c.actual_text or c.text or ""):
                    c.actual_text = said


def _say_table_marks(text: str, notes: dict) -> str:
    """A cell's symbols that have a note, said as footnotes: "120*" as "120 (footnote asterisk)"."""
    def say(m: re.Match) -> str:
        if m.group(0) not in notes:
            return m.group(0)
        return " (footnote " + " ".join(_TABLE_MARK_NAMES[ch] for ch in m.group(0)) + ")"
    out = re.sub(r"\s+", " ", _TABLE_MARK.sub(say, text)).strip()
    return out[1:-1] if out.startswith("(footnote") and out.endswith(")") and out.count("(") == 1 else out  # mark alone


def _lone_mark(page: PageAnalysis, b: TextBlock, marks: set[str]) -> TextBlock | None:
    """A footnote's number typeset with a gap after it comes out as a block of its own: a small block that is only a
    mark found in the text above, ending just left of b's first line."""
    for m in page.blocks:
        if (m is not b and m.role == "P" and m.text.strip() in marks and FOOTNOTE_MARK.match(m.text.strip())
                and b.baseline is not None and abs(m.bbox[3] - b.baseline) < 1.2 * b.font_size
                and m.bbox[2] <= b.bbox[2] - 10 and b.bbox[0] - m.bbox[2] < 25 and m.font_size <= b.font_size * 1.1):
            return m
    return None


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
        want_table = bool(_TABLE_CAPTION.match(cap.text))
        want_figure = bool(_FIGURE_CAPTION.match(cap.text))
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
    if len(text) >= 4:
        found = len(_gutters(text, page.width)) + 1
        if found > 1:
            return found
    # Short lines (a recipe, a price list) or columns under a title narrower than the page
    blocks = [(b.id, b.bbox) for b in page.blocks if b.role != "Artifact" and b.text.strip()]
    if len(blocks) < 8:
        return 1
    _, cols, gutters, _ = _columns_below(blocks, page.width)
    return len(gutters) + 1 if cols else 1


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
        lefts = [b for b in boxes if b[2] <= g]
        rights = [b for b in boxes if b[0] >= g]
        if not lefts or not rights:  # a margin, not a gap between things
            return 0
        spans = sorted((max(l[1], r[1]), min(l[3], r[3])) for l in lefts for r in rights
                       if min(l[3], r[3]) - max(l[1], r[1]) > 0)
        rows, end = 0, float("-inf")
        for top, bottom in spans:
            if top >= end:
                rows += 1
                end = bottom
            else:
                end = max(end, bottom)
        return rows
    return [g for g in gutters if rows_across(g) >= 2]


def _columns_below(items: list[tuple[str, BBox]], width: float) -> tuple[list, list, list[float], list]:
    """A band with nothing wide enough to count as full width, and no clear gutter: a title, a byline or a row of
    buttons narrower than the page can still sit across the top of two columns (a recipe's Ingredients beside its
    Directions), and something can sit across the bottom of them (a form's barcode, the next section's heading).
    Returns (what's above, the columns' items, their gutters, what's below): the highest place below which the rest
    is in columns, or failing that the tallest stretch that is. The columns must read down, not across: when most
    rows line up left and right (labels beside their values, a table without rules), reading across is right and
    nothing is split."""
    rows = sorted(items, key=lambda it: (it[1][1], it[1][0]))
    n = len(rows)
    if n < 7:
        return items, [], [], []
    # Worked out once, so each place to split costs a glance, not a search (a page of a textbook has hundreds of
    # blocks, and almost every gap between them is a place to try): the lowest bottom above each row, the highest
    # top and the left and right edges from each row down, and for each x the last row that crosses it.
    above_bottom = [float("-inf")] * (n + 1)
    for i, (_, b) in enumerate(rows):
        above_bottom[i + 1] = max(above_bottom[i], b[3])
    below_top, below_x0, below_x1 = [float("inf")] * (n + 1), [float("inf")] * (n + 1), [float("-inf")] * (n + 1)
    for i in range(n - 1, -1, -1):
        b = rows[i][1]
        below_top[i], below_x0[i], below_x1[i] = min(below_top[i + 1], b[1]), min(below_x0[i + 1], b[0]), max(below_x1[i + 1], b[2])
    step, lo, hi = 2.0, width * 0.15, width * 0.85
    xs = [lo + step * j for j in range(int((hi - lo) / step) + 1)]
    last_cross = [-1] * len(xs)
    for i, (_, b) in enumerate(rows):
        for j in range(max(0, int((b[0] - lo) / step)), min(len(xs), int((b[2] - lo) / step) + 2)):
            if b[0] < xs[j] < b[2]:
                last_cross[j] = i
    for k in range(1, n - 3):
        if above_bottom[k] > below_top[k] + 2:
            continue  # not a clean line between them
        # Is there a gap at least 8 points wide, with something either side, in what's below?
        run = best = 0
        for j, x in enumerate(xs):
            if last_cross[j] < k and below_x0[k] < x < below_x1[k]:
                run += 1
                best = max(best, run)
            else:
                run = 0
        if best * step < 8:
            continue
        above, below = rows[:k], rows[k:]
        gutters = _gutters([b for _, b in below], width)
        if not gutters:
            continue
        g = gutters[0]
        left = [it[1] for it in below if (it[1][0] + it[1][2]) / 2 < g]
        right = [it[1] for it in below if (it[1][0] + it[1][2]) / 2 >= g]
        if len(left) < 3 or len(right) < 3:
            continue
        if _reads_across([it[1] for it in below], g):
            return items, [], [], []
        return above, below, gutters, []

    # Nothing reaches the bottom in columns. Is there a stretch that is in columns, ending where something crosses
    # the gap? For each place to start, the furthest row before one crosses any 8-point-wide strip.
    crossing: list[list[int]] = [[] for _ in xs]
    for i, (_, b) in enumerate(rows):
        for j in range(max(0, int((b[0] - lo) / step)), min(len(xs), int((b[2] - lo) / step) + 2)):
            if b[0] < xs[j] < b[2]:
                crossing[j].append(i)
    # The widest gap first: a wide one that something soon crosses is the real gutter; a sliver that happens to run
    # further down (beside a heading that reaches most of the way across) isn't
    for gap in (24.0, 16.0, 8.0):
        need = int(gap / step)
        for k in range(1, n - 6):
            if above_bottom[k] > below_top[k] + 2:
                continue
            free_until = []
            for c in crossing:
                at = bisect.bisect_left(c, k)
                free_until.append(c[at] if at < len(c) else n)
            end = max((min(free_until[j:j + need]) for j in range(len(xs) - need + 1)), default=k)
            if end >= n or end - k < 6:
                continue
            middle, below = rows[k:end], rows[end:]
            if max(it[1][3] for it in middle) > min(it[1][1] for it in below) + 2:
                continue
            gutters = _gutters([b for _, b in middle], width)
            if not gutters:
                continue
            g = gutters[0]
            left = [it[1] for it in middle if (it[1][0] + it[1][2]) / 2 < g]
            right = [it[1] for it in middle if (it[1][0] + it[1][2]) / 2 >= g]
            if len(left) < 3 or len(right) < 3 or _reads_across([it[1] for it in middle], g):
                continue
            return rows[:k], middle, gutters, below
    return items, [], [], []


def _reads_across(boxes: list[BBox], gutter: float) -> bool:
    """Whether what's either side of a gap lines up row by row (labels beside values, a grid of exercise items,
    a table without rules), so it reads across, not down one side and then the other. Most items on BOTH sides
    need a partner starting at the same height: two columns of prose line up now and then by chance (and their
    headings always do), but never most of the way down both."""
    left = [b for b in boxes if (b[0] + b[2]) / 2 < gutter]
    right = [b for b in boxes if (b[0] + b[2]) / 2 >= gutter]
    if not left or not right:
        return False

    def partnered(side: list[BBox], other: list[BBox]) -> float:
        def same_row(a: BBox, b: BBox) -> bool:
            # a raised or lowered character (a power, a fraction) moves a line's top by a few points
            return abs(a[1] - b[1]) <= max(3.0, 0.5 * min(a[3] - a[1], b[3] - b[1]))
        return sum(1 for a in side if any(same_row(a, b) for b in other)) / len(side)

    return min(partnered(left, right), partnered(right, left)) >= 0.6


_RTL_RANGES = ((0x0590, 0x08FF), (0xFB1D, 0xFDFF), (0xFE70, 0xFEFF))  # Hebrew, Arabic, Syriac, Thaana; their forms


def right_to_left(text: str) -> bool:
    """Most of the letters are Arabic or Hebrew (or another right-to-left script): the page is read from the right
    (#368). A handful of letters decides nothing."""
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 20:
        return False
    rtl = sum(1 for c in letters if any(lo <= ord(c) <= hi for lo, hi in _RTL_RANGES))
    return rtl > len(letters) / 2


def _by_rows(items: list[tuple[str, BBox]], rtl: bool = False) -> list[tuple[str, BBox]]:
    """Top to bottom, and left to right within a row (right to left on a right-to-left page). Items whose tops are
    within a few points of the row's first item share the row: sorting on the exact top would let a point's
    difference put the right-hand item of a row first."""
    rest = sorted(items, key=lambda it: (it[1][1], it[1][0]))
    out: list[tuple[str, BBox]] = []
    while rest:
        first = rest[0]
        h = max(1.0, first[1][3] - first[1][1])
        tol = min(3.0, 0.35 * h)
        row = [it for it in rest if it[1][1] - first[1][1] <= tol]
        out += sorted(row, key=lambda it: -it[1][2]) if rtl else sorted(row, key=lambda it: it[1][0])
        rest = [it for it in rest if it not in row]
    return out


def heuristic_reading_order(page: PageAnalysis) -> list[str]:
    """Top-to-bottom bands separated by full-width elements; columns left-to-right inside a band (right to left on a
    page in Arabic or Hebrew, #368)."""
    items = sorted(_elements(page), key=lambda it: (round(it[1][1], 0), it[1][0]))
    if not items:
        return []
    rtl = right_to_left(" ".join(b.text for b in page.blocks))
    full = page.width * 0.6
    order: list[str] = []
    band: list[tuple[str, BBox]] = []

    def ordered(items: list[tuple[str, BBox]], depth: int = 0) -> list[str]:
        """Columns left to right, each read on its own (it may hold columns too: a logo beside a page that then
        splits in two); otherwise what's above any columns by rows, then those columns."""
        if depth > 3 or len(items) < 2:
            return [i for i, _ in _by_rows(items, rtl)]
        gutters = _gutters([b for _, b in items], page.width)
        if gutters and depth and _reads_across([b for _, b in items], gutters[0]):
            return [i for i, _ in _by_rows(items, rtl)]  # inside a column: labels beside their values, not more columns
        below: list[tuple[str, BBox]] = []
        if gutters:
            head, cols = [], items
        else:
            head, cols, gutters, below = _columns_below(items, page.width)
            if not cols:
                return [i for i, _ in _by_rows(items, rtl)]
        out = [i for i, _ in _by_rows(head, rtl)]
        edges = [float("-inf"), *gutters, float("inf")]
        columns = [[it for it in cols if lo <= (it[1][0] + it[1][2]) / 2 < hi] for lo, hi in zip(edges, edges[1:])]
        # A heading that starts below where every other column has ended, near the end of its own, opens what comes
        # after the columns (an article's first section under its sidebar and abstract): it, and what follows it,
        # is read last
        tail: list[tuple[str, BBox]] = []
        for n, col in enumerate(columns):
            others = [it[1][3] for m, c in enumerate(columns) if m != n for it in c]
            if not others:
                continue
            for it in sorted(col, key=lambda it: it[1][1]):
                el = page.element(it[0])
                if it[1][1] >= max(others) and str(getattr(el, "role", "")).startswith("H"):
                    rest = [x for x in col if x[1][1] >= it[1][1]]
                    # ...when it comes near the end of its column. A title with most of the column under it,
                    # beside a short neighbour (a logo), is the column's own start.
                    if len(rest) <= len(col) - len(rest):
                        tail = rest
                        columns[n] = [x for x in col if x[1][1] < it[1][1]]
                    break
            if tail:
                break
        for col in (reversed(columns) if rtl else columns):
            out += ordered(col, depth + 1)
        return out + [i for i, _ in _by_rows(tail, rtl)] + ordered(below, depth + 1)

    def flush() -> None:
        order.extend(ordered(band))
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
