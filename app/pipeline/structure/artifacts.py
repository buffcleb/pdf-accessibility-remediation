"""Decoration and page furniture: lines drawn with characters, running headers and footers, repeated
images, heading icons and the shapes behind short labels."""
from __future__ import annotations

import re
from collections import Counter

from app.pipeline.models import BBox, DocumentAnalysis, TextBlock
from app.pipeline.markers import PAGE_NUM


def _icon_shaped(bbox: BBox, page_width: float) -> bool:
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    return 12 <= min(w, h) and max(w, h) <= 0.1 * page_width and 0.5 <= w / max(h, 1e-6) <= 2.0


def mark_repeated_images(doc: DocumentAnalysis) -> None:
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


def mark_heading_icons(doc: DocumentAnalysis) -> None:
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


def mark_text_badges(doc: DocumentAnalysis) -> None:
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


_DRAWN = set("-‐‑‒–—―_.·…=~*")
# A word: three letters in a row (a contents line's title, a form's label), which a line drawn with characters hasn't
_WORD = re.compile(r"[^\W\d_]{3,}")


def mark_drawn_lines(doc: DocumentAnalysis) -> None:
    """A line drawn with characters (a dashed box border typed as a row of hyphens, a rule of underscores or dots) is
    decoration: an artifact, not a paragraph, a heading or a formula (a row of dashes with a dot in it was taken for a
    formula, #438). A line with words isn't, however long its dots or underscores: a table of contents entry with
    long dot leaders, or a form's label and the line to write on (#634). A word or two in a long rule (a letter set
    in a row of 200 dashes) leaves it a rule: the words have to be a twentieth of what's printed."""
    for p in doc.pages:
        for b in p.blocks:
            if b.role in ("Artifact", "Formula"):
                continue
            seen = [c for c in b.text if not c.isspace()]
            worded = _WORD.search(b.text) and sum(c.isalpha() for c in seen) >= 0.05 * len(seen)
            if not worded and len(seen) >= 8 and sum(c in _DRAWN for c in seen) >= 0.8 * len(seen):
                b.role = "Artifact"
                b.artifact_type = "Layout"


_DIGITS = re.compile(r"\d+")
_PAGE_OF = re.compile(r"\bpage\s+\d{1,5}\s*(?:of|/)\s*\d{1,5}\b", re.I)


def mark_running_artifacts(doc: DocumentAnalysis) -> None:
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
        if PAGE_NUM.match(b.text) or (doc.page_count >= 2 and counts[norm] >= threshold):
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
