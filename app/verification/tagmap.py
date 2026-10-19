"""Tag maps: where each structure element's content sits on the page, for the portal's tag manager.

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
from app.tagging.metadata import document_info
from app.tagging.table_headers import headers_named, id_of, table_attrs
from app.verification.integrity import TagIndex, _items, _is_elem, _mcid, check_integrity
from app.verification.semantic import SemanticChecker

SCHEMA = 1
TEXT_CHARS = 200
SAY_CHARS = 5000  # an element's own text in full, for reading aloud (where it differs from "text")
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


def _span(obj) -> list[int] | None:
    a = table_attrs(obj)
    if a is None:
        return None
    rs, cs = int(a.get("/RowSpan", 1) or 1), int(a.get("/ColSpan", 1) or 1)
    return [rs, cs] if rs > 1 or cs > 1 else None


def _union(a, b):
    if a is None:
        return b
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def page_sizes(path: Path) -> list[dict]:
    with fitz.open(path) as doc:
        return [{"w": round(p.rect.width, 2), "h": round(p.rect.height, 2)} for p in doc]


def _pieces(index: TagIndex, checker: SemanticChecker | None) -> list[dict]:
    """Each tagged piece of content in a page's own stream (one marked-content sequence): where it is, what it is
    and which tag holds it, so the editor can move pieces between tags."""
    if checker is None:
        return []
    out = []
    for (page, mcid), c in sorted(checker.by_mcid.items()):
        if page >= len(index.page_objgens) or not c.boxes:
            continue
        users = index.refs.get((index.page_objgens[page], mcid))
        if not users:
            continue
        box = c.boxes[0]
        for b in c.boxes[1:]:
            box = _union(box, b)
        kind = "text" if c.text else "image" if c.images else "drawing"
        out.append({"page": page + 1, "mcid": mcid, "el": users[0], "bbox": [round(v, 2) for v in box], "kind": kind,
                    **({"text": c.text[:PART_CHARS]} if c.text else {})})
    return out


def artifact_sections(path: Path) -> list[dict]:
    """The artifact sections of each page's own stream, numbered as the content marker and the editor number them,
    with where they are and what they draw, so the editor can tag one. A long document's pages are read by several
    processes (#124)."""
    from app.pipeline import page_pool

    with pikepdf.open(path) as probe:
        pages = len(probe.pages)
    workers = page_pool.workers_for(pages)
    done = page_pool.run("app.verification.tagmap:artifact_pages", path, pages, workers) if workers > 1 else None
    if done is None:
        done = list(artifact_pages(path, range(pages)).values())
    return [s for sections in done for s in sections]


def artifact_pages(path: Path, numbers) -> dict[int, list[dict]]:
    """artifact_sections for the pages numbered: {page (0-based): its sections}."""
    from app.tagging.content_marker import ContentMarker

    out: dict[int, list[dict]] = {}
    with pikepdf.open(path) as probe, fitz.open(path) as doc:
        for n in numbers:
            page, fp = probe.pages[n], doc[n]
            m = fp.transformation_matrix * fp.rotation_matrix
            marker = ContentMarker(probe, page, (m.a, m.b, m.c, m.d, m.e, m.f), lambda *a: ("artifact:Layout", "Artifact"))
            sections = out[n] = []
            for sm in marker.run().streams[:1]:  # the page's own stream
                for seq, info in sorted(sm.artifacts.items()):
                    if info["bbox"] is None:
                        continue
                    text = fp.get_textbox(fitz.Rect(info["bbox"])).strip() if "text" in info["kinds"] else ""
                    sections.append({"page": n + 1, "seq": seq, "bbox": [round(v, 2) for v in info["bbox"]],
                                     "kinds": sorted(info["kinds"]), "type": info["type"] or None,
                                     **({"nested": True} if info["nested"] else {}),
                                     **({"text": " ".join(text.split())[:PART_CHARS]} if text else {})})
    return out


def annotation_places(path: Path) -> dict[int, tuple[int, tuple, str | None]]:
    """Where each annotation sits (form fields, links): object number -> (page, box in the page's visual space, its
    name: a field's tooltip, a link's description). A tag that holds only an annotation has no content to place it
    by, so without this it has no box: it can't be shown on the page, and belongs to no page in particular."""
    out: dict[int, tuple[int, tuple, str | None]] = {}
    with pikepdf.open(path) as pdf, fitz.open(path) as doc:
        for n, page in enumerate(pdf.pages):
            fp = doc[n]
            m = fp.transformation_matrix * fp.rotation_matrix
            for a in page.get("/Annots") or []:
                rect = a.get("/Rect") if isinstance(a, pikepdf.Dictionary) and a.is_indirect else None
                if rect is None or len(rect) != 4:
                    continue
                r = (fitz.Rect(*(float(v) for v in rect)) * m).normalize()
                name = a.get("/TU") or (a.get("/Parent").get("/TU") if a.get("/Parent") is not None else None) \
                    or a.get("/Contents") or a.get("/T")
                out[a.objgen[0]] = (n, (r.x0, r.y0, r.x1, r.y1), _text(name))
    return out


def tagmap_from(pages: list[dict], index: TagIndex, checker: SemanticChecker | None,
                artifacts: list[dict] | None = None, annots: dict | None = None,
                document: dict | None = None) -> dict:
    els = index.elements
    # Own content per element and page, then folded up into ancestors. Children always have higher
    # pre-order indices than their parents, so one reverse pass is enough.
    own: list[dict[int, tuple]] = [{} for _ in els]
    if checker is not None:
        for idx, content in checker.own.items():
            for page, box in content.page_boxes:
                own[idx][page] = _union(own[idx].get(page), box)
    # A tag's annotations (a form field, a link) are part of what it holds: they give it a place too
    names: dict[int, str] = {}
    annotated: set[int] = set()
    for e in els:
        for it in _items(e.obj.get("/K")) if annots else []:
            target = it.get("/Obj") if isinstance(it, pikepdf.Dictionary) and not _is_elem(it) else None
            place = annots.get(target.objgen[0]) if target is not None and target.is_indirect else None
            if place is not None:
                own[e.index][place[0]] = _union(own[e.index].get(place[0]), place[1])
                annotated.add(e.index)
                if place[2]:
                    names.setdefault(e.index, place[2])
    subtree = [dict(o) for o in own]
    for e in reversed(els):
        if e.parent is not None:
            parent = subtree[e.parent]
            for page, box in subtree[e.index].items():
                parent[page] = _union(parent.get(page), box)

    # The header cells each cell names (Headers -> IDs -> elements), for the tag manager's cell headers
    by_id = {id_of(e.obj): e.index for e in els if id_of(e.obj)}
    # What each tag points at (/Ref: a footnote mark's note), by element
    by_obj = {e.obj.objgen: e.index for e in els if e.obj.is_indirect}
    refs = {e.index: [by_obj[r.objgen] for r in e.obj.Ref if isinstance(r, Dictionary) and r.is_indirect and r.objgen in by_obj]
            for e in els if isinstance(e.obj.get("/Ref"), Array)}
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
            "span": _span(e.obj) if e.role in ("TH", "TD") else None,  # [rows, columns] when more than one (#370)
            "headers": [by_id[h] for h in headers_named(e.obj) if h in by_id] or None if e.role in ("TH", "TD") else None,
            "refs": refs.get(e.index) or None,
            "text": text,
            # what a screen reader announces for a form field or link that has no text of its own
            "name": names.get(e.index) if not text else None,
            "annot": True if e.index in annotated else None,  # it holds a form field, link or other annotation
            "order": None,
            "parts": _parts(e, index, checker),
        }
        if has_own and checker is not None:
            say = checker.own[e.index].text[:SAY_CHARS] if e.index in checker.own else ""
            if say != (text or ""):
                item["say"] = say  # empty when all its text is in child tags
        if has_own:
            item["order"] = order
            order += 1
        out.append({k: v for k, v in item.items() if v is not None or k in ("parent", "order")})
    return {"schema": SCHEMA, "pages": pages, "elements": out, "pieces": _pieces(index, checker),
            "artifacts": artifacts or [], "document": document or {"title": None, "lang": None}}


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
            mcid = _mcid(item)
            if mcid is None:
                continue  # an id that isn't an integer names no piece
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
            return {"schema": SCHEMA, "pages": pages, "elements": [], "document": document_info(pdf)}
        checker = SemanticChecker(path, pdf, index, get_settings().verify_max_pages)
        checker._collect()
        return tagmap_from(pages, index, checker, artifact_sections(path), annotation_places(path),
                           document_info(pdf))
