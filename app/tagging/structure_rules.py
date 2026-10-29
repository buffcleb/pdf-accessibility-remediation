"""Tags at the right level, and figures that say where they are (#437, #438): the rules of the standard structure
types that PAC checks and veraPDF's PDF/UA-1 profile doesn't.

Standard structure types are block-level (P, H1-H6, L, Table, Caption…) or inline (Span, Quote, Note, Reference, Link,
Annot…); Figure, Formula and Form are inline unless their layout attributes say /Placement /Block. A grouping tag
(Document, Part, Art, Sect, Div) holds block-level tags. So, over the finished tree (ours or the document's own):

- a Document that isn't the root becomes a Part (a file put together from several keeps each one's Document);
- a Part anywhere but straight in the Document becomes a Sect: PAC flags a Part inside another (PowerPoint's
  Slide maps to Part, so a deck put inside a longer document is 245 Parts in a Part on a 332-page one);
- a P holding a P: the outer one becomes a Div when it has no content of its own, otherwise the inner one a Span;
- inline tags standing directly in a grouping tag are put in a P, a run of them together (a Span, a Link and a
  Span are one paragraph);
- a Figure, Formula or Form standing directly in a grouping tag is a block: /Placement /Block;
- a Figure or Formula on one page gets its bounding box (/BBox, in the page's user space) where its place is known;
- a Table with a TBody but no THead or TFoot: its leading rows of header cells become a THead, or, when it has none,
  the TBody's rows stand in the Table itself (PAC flags a TBody with nothing to tell it from: 43 of the 89 TBody on
  a 332-page document, each in a table with no THead).

Only roles and nesting change, and attributes are added: no marked content moves, so the parent tree (which maps
each piece of content to the tag holding it) stays as it is.
"""
from __future__ import annotations

import pikepdf
from pikepdf import Array, Dictionary, Name

from app.tagging.existing_tags import resolve_role
from app.tagging.tree import kids as _kids

GROUPING = {"Document", "Part", "Art", "Sect", "Div"}
INLINE = {"Span", "Quote", "Note", "Reference", "BibEntry", "Code", "Link", "Annot", "Ruby", "Warichu"}
ILLUSTRATION = {"Figure", "Formula", "Form"}


def _is_elem(o) -> bool:
    return isinstance(o, Dictionary) and "/S" in o and o.get("/Type", Name.StructElem) == Name.StructElem


def _set_kids(e, kids: list) -> None:
    e.K = Array(kids)


def _own_content(e) -> bool:
    """Marked content or an annotation of its own, not only tags."""
    return any(not _is_elem(k) for k in _kids(e))


def _layout(e) -> Dictionary:
    """The element's Layout attribute dictionary, made (beside any others it has) if it has none."""
    a = e.get("/A")
    found = [d for d in (a if isinstance(a, Array) else [a])
             if isinstance(d, Dictionary) and d.get("/O") == Name.Layout]
    if found:
        return found[0]
    new = Dictionary(O=Name.Layout)
    if a is None:
        e.A = new
    elif isinstance(a, Array):
        a.append(new)
    else:
        e.A = Array([a, new])
    return new


def _has(e, key: str) -> bool:
    a = e.get("/A")
    return any(isinstance(d, Dictionary) and key in d for d in (a if isinstance(a, Array) else [a]))


def _user_bbox(m: tuple, box) -> list[float]:
    """A box in the page's visual space (PyMuPDF's, as the analysis has it) back in its user space: the inverse of
    m, the matrix taking user space to visual space."""
    a, b, c, d, e, f = (float(v) for v in m)  # plain numbers: a box can come from numpy (a music or scan figure)
    det = a * d - b * c
    pts = []
    x0b, y0b, x1b, y1b = (float(v) for v in box)
    for x, y in ((x0b, y0b), (x1b, y0b), (x0b, y1b), (x1b, y1b)):
        x0, y0 = x - e, y - f
        pts.append(((d * x0 - c * y0) / det, (-b * x0 + a * y0) / det))
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return [round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2)]


def bboxes_from_tagmap(pdf: pikepdf.Pdf, tagmap: dict, matrices: list) -> int:
    """Figures and formulas on one page with no /BBox get theirs from where the tag map puts them: for a file the tag
    editor changed, which makes tags (a figure out of an area, a part of a picture, a type changed to Figure) without
    knowing where content is drawn. tagmap: build_tagmap's, of this file; matrices: each page's user-to-visual
    matrix. The tag map's elements are the tree's walk, in order; a tree that doesn't match it is left alone."""
    from app.tagging.tree import walk
    root = pdf.Root.get("/StructTreeRoot")
    els = tagmap.get("elements") or []
    tree = walk(root) if isinstance(root, Dictionary) else []
    if len(tree) != len(els):
        return 0
    given = 0
    for e, el in zip(tree, els):
        boxes = el.get("boxes") or []
        if el.get("role") in ("Figure", "Formula") and len(boxes) == 1 and not _has(e, "/BBox"):
            pi = boxes[0]["page"] - 1
            if 0 <= pi < len(matrices):
                _layout(e).BBox = Array(_user_bbox(matrices[pi], boxes[0]["bbox"]))
                given += 1
    return given


def apply(pdf: pikepdf.Pdf, boxes: dict | None = None, matrices: list | None = None) -> dict:
    """The rules above, over the whole tree. boxes: objgen -> (page index, box in visual space) for figures and
    formulas whose place is known; matrices: each page's user-to-visual matrix. Returns what was done, by kind."""
    done = {"documents_made_parts": 0, "parts_made_sections": 0, "paragraphs_made_groups": 0, "paragraphs_made_spans": 0,
            "inline_put_in_paragraphs": 0, "figures_placed_as_blocks": 0, "figures_given_bbox": 0,
            "header_rows_made_thead": 0, "table_bodies_unwrapped": 0}
    root = pdf.Root.get("/StructTreeRoot")
    if not isinstance(root, Dictionary):
        return done
    rm = root.get("/RoleMap")
    role_map = {str(k).lstrip("/"): str(v).lstrip("/") for k, v in rm.items()} if isinstance(rm, Dictionary) else {}
    role = lambda e: resolve_role(str(e.get("/S", "")).lstrip("/"), role_map)  # noqa: E731
    boxes, matrices = boxes or {}, matrices or []

    def walk(e, parent_role: str | None, seen: set) -> None:
        if e.objgen in seen:
            return
        seen.add(e.objgen)
        r = role(e)
        if r == "Document" and parent_role is not None:
            e.S = Name.Part
            r = "Part"
            done["documents_made_parts"] += 1
        elif r == "Part" and parent_role not in (None, "Document"):
            e.S = Name.Sect
            r = "Sect"
            done["parts_made_sections"] += 1
        if r == "P":
            inner = [k for k in _kids(e) if _is_elem(k) and role(k) == "P"]
            if inner and not _own_content(e):
                e.S = Name.Div
                r = "Div"
                done["paragraphs_made_groups"] += 1
            else:
                for k in inner:
                    k.S = Name.Span
                    done["paragraphs_made_spans"] += 1
        if r in ILLUSTRATION and parent_role in GROUPING and not _has(e, "/Placement"):
            _layout(e).Placement = Name.Block
            done["figures_placed_as_blocks"] += 1
        if r in ("Figure", "Formula") and e.objgen in boxes and not _has(e, "/BBox"):
            pi, box = boxes[e.objgen]
            if pi is not None and box is not None and pi < len(matrices):
                _layout(e).BBox = Array(_user_bbox(matrices[pi], box))
                done["figures_given_bbox"] += 1
        if r in GROUPING:
            _wrap_inline(pdf, e, role, done)
        if r == "Table":
            _table_groups(pdf, e, role, done)
        for k in _kids(e):
            if _is_elem(k):
                walk(k, r, seen)

    seen: set = set()
    for top in _kids(root):
        if _is_elem(top):
            walk(top, None, seen)
    return done


def _wrap_inline(pdf: pikepdf.Pdf, group, role, done: dict) -> None:
    """Runs of inline tags standing directly in a grouping tag, each put in a new P where the run was."""
    kids, out, run = _kids(group), [], []

    def flush():
        if not run:
            return
        page = next((k.get("/Pg") for k in run if k.get("/Pg") is not None), None)
        para = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.P, P=group, K=Array(run),
                                            **({"Pg": page} if page is not None else {})))
        for k in run:
            k.P = para
        out.append(para)
        done["inline_put_in_paragraphs"] += len(run)
        run.clear()

    changed = False
    for k in kids:
        if _is_elem(k) and role(k) in INLINE:
            run.append(k)
            changed = True
        else:
            flush()
            out.append(k)
    flush()
    if changed:
        _set_kids(group, out)


def _table_groups(pdf: pikepdf.Pdf, table, role, done: dict) -> None:
    """A Table whose rows are grouped in a TBody with no THead or TFoot beside it: its leading header rows (every
    cell a TH) made a THead before the body, or else the body's rows put in the Table where the TBody was."""
    kids = _kids(table)
    roles = [role(k) if _is_elem(k) else None for k in kids]
    if "TBody" not in roles or "THead" in roles or "TFoot" in roles:
        return
    bodies = [k for k, r in zip(kids, roles) if r == "TBody"]
    rows = [r for b in bodies for r in _kids(b) if _is_elem(r)]

    def header(row) -> bool:
        if role(row) != "TR":
            return False
        cells = [role(c) for c in _kids(row) if _is_elem(c)]
        return bool(cells) and all(c == "TH" for c in cells)

    first = _kids(bodies[0])
    lead = []
    for k in first:
        if not (_is_elem(k) and header(k)):
            break
        lead.append(k)
    if lead and len(lead) < len(rows):
        page = next((r.get("/Pg") for r in lead if r.get("/Pg") is not None), None)
        head = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.THead, P=table, K=Array(lead),
                                            **({"Pg": page} if page is not None else {})))
        for r in lead:
            r.P = head
        _set_kids(bodies[0], first[len(lead):])
        at = kids.index(bodies[0])
        _set_kids(table, kids[:at] + [head] + kids[at:])
        done["header_rows_made_thead"] += len(lead)
        return
    out = []
    for k, r in zip(kids, roles):
        if r != "TBody":
            out.append(k)
            continue
        for row in _kids(k):
            if _is_elem(row):
                row.P = table
            out.append(row)
        done["table_bodies_unwrapped"] += 1
    _set_kids(table, out)
