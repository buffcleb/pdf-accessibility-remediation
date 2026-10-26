"""Empty spans in kept tags.

Layout programs leave Span tags around nothing a reader would notice: the tab at the end of a form label, a space
between two styled runs. Each is one more stop in the tag tree with nothing to say. Their content joins the tag
they're in (it's still tagged, and the page is untouched) and the Span is removed.

Only a Span with no tags inside it, no annotation, nothing to say in its Alt, ActualText or expansion (a line break
or a space as ActualText is still nothing), and content that is text alone (never a picture or a drawing) with no
visible characters is touched.
"""
from __future__ import annotations

import unicodedata
from pathlib import Path

import pikepdf
import pymupdf as fitz
from pikepdf import Array, Dictionary

from app.tagging.content_marker import mcid_of
from app.tagging.existing_tags import _is_struct_elem, resolve_role
from app.tagging.tree import items as _items, new_mcr


def _visible(text: str) -> bool:
    """Something a reader would see or hear: not spaces, tabs, line breaks, or zero-width or control characters."""
    return any(c.strip() and unicodedata.category(c) not in ("Cf", "Cc", "Zs", "Zl", "Zp") for c in text)


def remove_empty_spans(pdf: pikepdf.Pdf, src: Path, existing_bbox: dict, existing_kinds: dict,
                       role_map: dict[str, str]) -> int:
    """existing_bbox: page objgen -> {MCID: bbox on the page}, existing_kinds: page objgen -> {MCID: what it draws},
    both from the content marker. Returns how many Spans were removed."""
    root = pdf.Root.get("/StructTreeRoot")
    if root is None:
        return 0
    page_index = {p.objgen: i for i, p in enumerate(pdf.pages)}
    doc = None
    chars: dict[int, list[tuple[float, float]]] = {}  # page -> centers of its visible characters

    def has_ink(page: int, bbox) -> bool:
        nonlocal doc
        if page not in chars:
            if doc is None:
                doc = fitz.open(src)
            pts = []
            for block in doc[page].get_text("rawdict").get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        for ch in span.get("chars", []):
                            if _visible(ch["c"]):
                                b = ch["bbox"]
                                pts.append(((b[0] + b[2]) / 2, (b[1] + b[3]) / 2))
            chars[page] = pts
        return any(bbox[0] - 0.5 <= x <= bbox[2] + 0.5 and bbox[1] - 0.5 <= y <= bbox[3] + 0.5 for x, y in chars[page])

    def empty(span) -> bool:
        if "/ID" in span or any(_visible(str(span.get(k))) for k in ("/Alt", "/ActualText", "/E") if k in span):
            return False
        items = _items(span.get("/K"))
        if not items:
            return False  # nothing at all: the repair of kept tags deals with those
        pg = span.get("/Pg")
        for it in items:
            if isinstance(it, Dictionary):
                if "/MCID" not in it or "/Stm" in it:
                    return False  # a tag, an annotation, or content in a form
                owner, mcid = it.get("/Pg") or pg, mcid_of(it)
                if mcid is None:
                    return False  # an id that isn't an integer: the repair of kept tags deals with it
            else:
                try:
                    owner, mcid = pg, int(it)
                except (TypeError, ValueError):
                    return False
            if owner is None or not owner.is_indirect or owner.objgen not in page_index:
                return False
            if not existing_kinds.get(owner.objgen, {}).get(mcid, set()) <= {"text"}:
                return False  # a picture or a line: not ours to judge
            bbox = existing_bbox.get(owner.objgen, {}).get(mcid)
            if bbox is not None and has_ink(page_index[owner.objgen], bbox):
                return False
        return True

    removed = 0

    def walk(e, depth: int = 0) -> None:
        nonlocal removed
        if depth > 200:
            return
        kids = _items(e.get("/K"))
        out, changed = [], False
        for k in kids:
            if not (isinstance(k, Dictionary) and _is_struct_elem(k)):
                out.append(k)
                continue
            if resolve_role(str(k.get("/S", ""))[1:], role_map) == "Span" and _is_struct_elem(e) and empty(k):
                # Its content joins this tag, naming its page (this tag may be on another)
                pg = k.get("/Pg")
                for it in _items(k.get("/K")):
                    if isinstance(it, Dictionary):
                        if "/Pg" not in it and pg is not None:
                            it.Pg = pg
                        out.append(it)
                    else:
                        out.append(new_mcr(pg, int(it)))
                removed += 1
                changed = True
                continue
            walk(k, depth + 1)
            out.append(k)
        if changed:
            e.K = Array(out)
    try:
        for top in _items(root.get("/K")):
            if isinstance(top, Dictionary):
                walk(top)
    finally:
        if doc is not None:
            doc.close()
    return removed
