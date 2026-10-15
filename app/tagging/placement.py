"""Where added tags go in a document whose tags are kept.

When a document's existing tags are kept, the engine still adds some of its own: content the producer's tags left
out (a block of contact details, a caption), form fields and links that had no tag. They used to be added at the top
of the tree after the producer's content, so a form's page-1 phone number was read after page 6, and its fields
after everything. Here each added tag is put beside the kept tag it follows on its page:

- content goes by its rank in the page's reading order (the same measure the reordering of kept tags uses): right
  after the kept tag with the nearest rank at or before its own, or before the page's first kept tag when it comes
  before them all;
- a form field, link or other annotation goes by where its rectangle sits among the tags on its page: a small box
  (a checkbox, a radio button) just before the text to its right on the same line; anything else right after the
  nearest text (the label beside, above or under a field);
- as high up the tree from there as it takes to be valid: a paragraph can't sit in a table row or between a list's
  items, so it follows the table or the list. A form field may also sit inside a table cell or a list item's
  body, with its label;
- added blocks the layout typed as headings by their size become paragraphs when the document has headings of its
  own: the producer's outline stands, and "may be a missed heading" stays a review item.

Pages with no kept tags are left as they were (added after the last earlier page's content).
"""
from __future__ import annotations

import pikepdf
from pikepdf import Array, Dictionary, Name

from app.pipeline.models import DocumentAnalysis
from app.tagging.content_marker import apply
from app.tagging.existing_tags import _is_struct_elem
from app.tagging.reorder import Reorderer, _items

# Where added tags may sit: the document's main groupings. Not an aside, a quotation or a "no structure" group:
# what the producer set apart stays as the producer made it.
_PARENTS = {"Document", "Part", "Art", "Sect", "Div"}
# A form field can also sit in the cell or list item that holds its label
_FIELD_PARENTS = _PARENTS | {"LBody", "TD", "TH"}


def _key(e) -> tuple:
    return e.objgen if e.is_indirect else ("direct", id(e))


def place_added(pdf: pikepdf.Pdf, analysis: DocumentAnalysis, marks: dict, role_map: dict[str, str],
                added: list) -> dict[str, int]:
    """Move the tags the engine added (as top-level children of the document tag) beside the kept tags they follow.
    Returns {"placed": tags moved, "headings_demoted": added headings made paragraphs}."""
    out = {"placed": 0, "headings_demoted": 0}
    root = pdf.Root.get("/StructTreeRoot")
    if root is None or not added:
        return out
    r = Reorderer(pdf, analysis, marks, role_map)
    new = {_key(e) for e in added}

    # The kept tags with content of their own, in tree order, with their page and rank; and whether the
    # document has headings of its own
    kept: dict[int, list[tuple[float, int, pikepdf.Object]]] = {}
    has_headings = False
    n = 0

    def walk(e) -> None:
        nonlocal n, has_headings
        for k in _items(e.get("/K")):
            if not _is_struct_elem(k) or _key(k) in new:
                continue
            role = r.role(k)
            if len(role) == 2 and role[0] == "H" and role[1].isdigit():
                has_headings = True
            page, box = r.own_box(k)
            if page is not None and box is not None:
                rank = r._rank(page, box)
                if rank is not None:
                    n += 1
                    kept.setdefault(page, []).append((rank, n, k))
            walk(k)
    for top in _items(root.get("/K")):
        if _is_struct_elem(top):
            walk(top)

    def annot_box(e) -> tuple[int | None, tuple | None]:
        for it in _items(e.get("/K")):
            if isinstance(it, Dictionary) and "/Obj" in it:
                annot, pg = it.Obj, it.get("/Pg") or e.get("/Pg")
                page = r.page_index.get(pg.objgen) if pg is not None and pg.is_indirect else None
                rect = annot.get("/Rect") if isinstance(annot, Dictionary) else None
                if page is None or rect is None or len(rect) != 4:
                    return None, None
                m = analysis.pages[page].pdf_to_page
                x0, y0 = apply(m, float(rect[0]), float(rect[1]))
                x1, y1 = apply(m, float(rect[2]), float(rect[3]))
                return page, (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        return None, None

    def fits(role: str, parent) -> bool:
        return r.role(parent) in (_FIELD_PARENTS if role == "Form" else _PARENTS)

    last_after: dict[tuple, pikepdf.Object] = {}  # tag -> the last added tag put after it

    def put(e, anchor, after: bool) -> bool:
        """Beside the anchor, up the tree to the first place this tag may sit."""
        role = r.role(e)
        node = last_after.get(_key(anchor), anchor) if after else anchor
        parent = node.get("/P")
        while isinstance(parent, Dictionary) and _is_struct_elem(parent) and not fits(role, parent):
            node, parent = parent, parent.get("/P")
        if not isinstance(parent, Dictionary) or not _is_struct_elem(parent):
            return False  # nowhere valid beside it: stays where it was added
        old = e.get("/P")
        if isinstance(old, Dictionary):
            old.K = Array([k for k in _items(old.get("/K")) if not (_is_struct_elem(k) and _key(k) == _key(e))])
        kids = _items(parent.get("/K"))
        at = next((i for i, k in enumerate(kids) if _is_struct_elem(k) and _key(k) == _key(node)), None)
        if at is None:
            kids.append(e)
        else:
            kids.insert(at + 1 if after else at, e)
        parent.K = Array(kids)
        e.P = parent
        if after:
            last_after[_key(anchor)] = e
        out["placed"] += 1
        return True

    # 1. Content, in reading order (so tags that follow the same kept tag keep their own order)
    content, annots = [], []
    for seq, e in enumerate(added):
        pages, rank = r.info(e)
        if rank is not None and len(pages) == 1:
            content.append((next(iter(pages)), rank, seq, e))
        else:
            page, box = annot_box(e)
            if page is not None:
                annots.append((page, box[1], box[0], seq, e, box))
    content.sort(key=lambda t: t[:3])
    # Text the page's own tags left out can be a field's label: once placed, it's something to sit beside
    texts: dict[int, list[tuple[tuple, pikepdf.Object]]] = {}
    for page, lst in kept.items():
        for _, _, k in lst:
            texts.setdefault(page, []).append((r.own_box(k)[1], k))
    for page, rank, _, e in content:
        role = r.role(e)
        if has_headings and len(role) == 2 and role[0] == "H" and role[1].isdigit():
            e.S = Name.P
            out["headings_demoted"] += 1
        if not kept.get(page):
            continue
        before = [t for t in kept[page] if t[0] <= rank]
        if before:
            ok = put(e, max(before, key=lambda t: (t[0], t[1]))[2], True)
        else:
            ok = put(e, min(kept[page], key=lambda t: (t[0], t[1]))[2], False)
        box = r.own_box(e)[1]
        if ok and box is not None:
            texts.setdefault(page, []).append((box, e))

    # 2. Fields, links and other annotations, top to bottom and left to right
    annots.sort(key=lambda t: t[:4])
    for page, _, _, _, e, box in annots:
        near = texts.get(page)
        if not near:
            continue
        cy, small = (box[1] + box[3]) / 2, box[2] - box[0] <= 30 and box[3] - box[1] <= 30
        right = [(b[0] - box[2], k) for b, k in near if b[1] - 2 <= cy <= b[3] + 2 and -4 <= b[0] - box[2] <= 40]
        if small and right:
            put(e, min(right, key=lambda t: t[0])[1], False)  # a checkbox, then the words it's for
            continue

        def gap(b) -> float:
            dx = max(b[0] - box[2], box[0] - b[2], 0.0)
            dy = max(b[1] - box[3], box[1] - b[3], 0.0)
            return (dx * dx + dy * dy) ** 0.5
        b, k = min(near, key=lambda t: gap(t[0]))
        if gap(b) <= 72:
            put(e, k, True)
    return out
