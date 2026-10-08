"""Tag maps: where each structure element's content sits on the page, for the portal's tag viewer.

Element indices are the same pre-order indices the verification findings carry (`element`), so the
viewer can join findings to the boxes it draws. Boxes are in visual page space (points, origin top
left, page rotation applied), matching what a viewer renders.
"""
from __future__ import annotations

from pathlib import Path

import pikepdf
import pymupdf as fitz
from pikepdf import Array, Dictionary

from app.config import get_settings
from app.verification.integrity import TagIndex, _items, _is_elem, check_integrity
from app.verification.semantic import SemanticChecker

SCHEMA = 1
TEXT_CHARS = 200
PART_CHARS = 80


def _text(value) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _scope(obj) -> str | None:
    """/Scope from the element's table attributes (/A may be a dictionary or an array of them)."""
    attrs = obj.get("/A")
    items = list(attrs) if isinstance(attrs, Array) else [attrs]
    for a in items:
        if isinstance(a, Dictionary) and "/Scope" in a:
            return str(a.Scope)[1:]
    return None


def _union(a, b):
    if a is None:
        return b
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def page_sizes(path: Path) -> list[dict]:
    with fitz.open(path) as doc:
        return [{"w": round(p.rect.width, 2), "h": round(p.rect.height, 2)} for p in doc]


def tagmap_from(pages: list[dict], index: TagIndex, checker: SemanticChecker | None) -> dict:
    els = index.elements
    # Own content per element and page, then folded up into ancestors. Children always have higher
    # pre-order indices than their parents, so one reverse pass is enough.
    own: list[dict[int, tuple]] = [{} for _ in els]
    if checker is not None:
        for idx, content in checker.own.items():
            for page, box in content.page_boxes:
                own[idx][page] = _union(own[idx].get(page), box)
    subtree = [dict(o) for o in own]
    for e in reversed(els):
        if e.parent is not None:
            parent = subtree[e.parent]
            for page, box in subtree[e.index].items():
                parent[page] = _union(parent.get(page), box)

    order = 0
    out = []
    for e in els:
        has_own = bool(own[e.index])
        text = None
        if checker is not None:
            text = checker.subtree(e).text[:TEXT_CHARS] or None
        page = e.page if e.page is not None else (min(subtree[e.index]) if subtree[e.index] else None)
        item = {
            "i": e.index,
            "parent": e.parent,
            "kids": e.kids,
            "role": e.role,
            "raw_role": e.raw_role if e.raw_role != e.role else None,
            "page": page + 1 if page is not None else None,
            "boxes": [{"page": p + 1, "bbox": [round(v, 2) for v in b]} for p, b in sorted(subtree[e.index].items())],
            "own": has_own,
            "alt": _text(e.obj.get("/Alt")),
            "actual_text": _text(e.obj.get("/ActualText")),
            "lang": _text(e.obj.get("/Lang")),
            "scope": _scope(e.obj) if e.role in ("TH", "TD") else None,
            "text": text,
            "order": None,
            "parts": _parts(e, index, checker),
        }
        if has_own:
            item["order"] = order
            order += 1
        out.append({k: v for k, v in item.items() if v is not None or k in ("parent", "order")})
    return {"schema": SCHEMA, "pages": pages, "elements": out}


def _parts(e, index: TagIndex, checker: SemanticChecker | None) -> list[dict] | None:
    """The element's /K items in order, for splitting a tag: content pieces, child tags and links.
    Only for elements with more than one item."""
    items = _items(e.obj.get("/K"))
    if len(items) < 2:
        return None
    page_of = {og: n for n, og in enumerate(index.page_objgens)}
    kids = iter(e.kids)
    out = []
    for k, item in enumerate(items):
        if _is_elem(item):
            child = next(kids, None)
            out.append({"k": k, "kind": "tag", "el": child})
            continue
        if isinstance(item, int) or (hasattr(item, "get") and "/MCID" in item):
            mcid = int(item) if isinstance(item, int) else int(item.MCID)
            page = e.page
            if not isinstance(item, int) and item.get("/Pg") is not None:
                page = page_of.get(item.Pg.objgen, page)
            piece = checker.by_mcid.get((page, mcid)) if checker is not None and page is not None else None
            part = {"k": k, "kind": "content", "page": page + 1 if page is not None else None}
            if piece is not None:
                if piece.bbox:
                    part["bbox"] = [round(v, 2) for v in piece.bbox]
                txt = piece.text
                if txt:
                    part["text"] = txt[:PART_CHARS]
                elif piece.images:
                    part["text"] = "(image)"
                elif piece.graphics:
                    part["text"] = "(drawing)"
            out.append(part)
            continue
        out.append({"k": k, "kind": "link"})
    return out


def build_tagmap(path: Path) -> dict:
    """Tag map for any PDF. Untagged or broken trees give the elements that could be read."""
    path = Path(path)
    pages = page_sizes(path)
    with pikepdf.open(path) as pdf:
        _, index = check_integrity(pdf)
        if not index.elements:
            return {"schema": SCHEMA, "pages": pages, "elements": []}
        checker = SemanticChecker(path, pdf, index, get_settings().verify_max_pages)
        checker._collect()
        return tagmap_from(pages, index, checker)
