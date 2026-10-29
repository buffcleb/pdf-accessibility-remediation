"""Table cells and the headers they name (Headers and ID), a tag pointing at its note, and the table editor."""
from __future__ import annotations

from dataclasses import dataclass

from pikepdf import Array, Dictionary, Name, String

from app.tagging.editor._ctx import (
    LIST_ITEM_FROM, NOTES, EditError, _absorbed, _ancestors, _at_root, _Ctx, _detach, _elem, _give_id, _parent_obj,
    _reparent_items, _role_of, _same, register,
)
from app.tagging.editor.areas import _split_inside
from app.tagging.editor.structure import _split
from app.tagging.role_rules import check_move
from app.tagging.table_headers import header_links, headers_named, id_of, is_complex, table_attrs
from app.tagging.tree import is_elem, new_elem, walk
from app.tagging.tree import items as _items
from app.verification.integrity import Elem

MAX_TABLE_ROWS, MAX_TABLE_COLS = 500, 60
MAX_HEADERS = 50
SCOPES = {"Row", "Column", "Both"}


def _check_table_rows(op: dict, n: int) -> None:
    """The shape of a table edit's rows, before the PDF is opened."""
    whole = lambda v, lo=0: isinstance(v, int) and not isinstance(v, bool) and v >= lo  # noqa: E731
    rows, key = op.get("rows"), "from" if op["op"] == "rebuild_table" else "parts"
    if not isinstance(rows, list) or not rows or len(rows) > MAX_TABLE_ROWS:
        raise EditError(f"Edit {n}: a table needs its rows (at most {MAX_TABLE_ROWS}).")
    seen: set = set()
    for row in rows:
        if not isinstance(row, list) or len(row) > MAX_TABLE_COLS:
            raise EditError(f"Edit {n}: each row of a table is a list of its cells (at most {MAX_TABLE_COLS}).")
        for cell in row:
            if (not isinstance(cell, dict) or cell.get("role") not in ("TH", "TD")
                    or (cell["role"] == "TH" and cell.get("scope", "Column") not in SCOPES)
                    or not whole(cell.get("rows", 1), 1) or not whole(cell.get("cols", 1), 1)
                    or cell.get("rows", 1) > MAX_TABLE_ROWS or cell.get("cols", 1) > MAX_TABLE_COLS
                    or not isinstance(cell.get(key), list)
                    or not isinstance(cell.get("text", ""), str) or len(cell.get("text", "")) > 4000):
                raise EditError(f"Edit {n}: each cell is a header (TH, with a scope) or data cell (TD), with its "
                                f"spans and what it holds.")
            for ref in cell[key]:
                if key == "from":
                    _check_from(ref, seen, op, n)
                else:
                    others = op.get("absorb") or []
                    if (not isinstance(ref, dict) or not whole(ref.get("at")) or ("el" in ref and not whole(ref["el"]))
                            or ("el" in ref and ref["el"] != op["element"] and ref["el"] not in others)):
                        raise EditError(f"Edit {n}: a cell's pieces are positions in the tags being made a table.")
                    k = (ref.get("el", op["element"]), ref["at"])
                    if k in seen:
                        raise EditError(f"Edit {n}: each piece can go in one cell only.")
                    seen.add(k)
    if op["op"] == "rebuild_table":
        cuts = op.get("cuts") or {}
        if (not isinstance(cuts, dict) or len(cuts) > 200
                or not all(str(k).isdigit() and isinstance(v, list) and 1 <= len(v) <= 50
                           and all(isinstance(x, dict) and whole(x.get("at")) and whole(x.get("char", 0)) for x in v)
                           for k, v in cuts.items())):
            raise EditError(f"Edit {n}: a cell's text is cut at places in its pieces of text.")
        out = op.get("out") or []
        if not isinstance(out, list) or len(out) > 50:
            raise EditError(f"Edit {n}: what comes out of the table is a list of paragraphs.")
        for para in out:
            if not isinstance(para, dict) or para.get("role", "P") not in ("P", "Caption", "Note") \
                    or not isinstance(para.get("from"), list) or not para["from"]:
                raise EditError(f"Edit {n}: each paragraph out of the table names the cells it takes.")
            for ref in para["from"]:
                _check_from(ref, seen, op, n)
        for k, v in cuts.items():  # a cut cell goes in by its parts, every one of them
            parts = {r[1] for r in seen if isinstance(r, tuple) and r[0] == int(k)}
            if int(k) in seen or parts != set(range(len(v) + 1)):
                raise EditError(f"Edit {n}: each part of a cell whose text is cut goes in a cell (or a paragraph) "
                                "of its own, and every part goes somewhere.")
    caption = op.get("caption")
    if caption is not None and not whole(caption):
        raise EditError(f"Edit {n}: a table's caption is a tag (or none).")
    absorb = op.get("absorb") or []
    if not isinstance(absorb, list) or len(absorb) > 500 or op["element"] in absorb or len(set(absorb)) != len(absorb) \
            or not all(whole(a) for a in absorb):
        raise EditError(f"Edit {n}: the other tags to make a table of are a list of tags.")


def _check_from(ref, seen: set, op: dict, n: int) -> None:
    """An old cell a new cell takes: its number, or [its number, a part] when its text is cut (op "cuts")."""
    whole = lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0  # noqa: E731
    cut = isinstance(ref, list) and len(ref) == 2 and all(whole(v) for v in ref)
    if not (whole(ref) or cut):
        raise EditError(f"Edit {n}: each cell names the old cells it takes.")
    key = (ref[0], ref[1]) if cut else ref
    if cut and str(ref[0]) not in (op.get("cuts") or {}):
        raise EditError(f"Edit {n}: a part of a cell is named only when its text is cut.")
    if key in seen:
        raise EditError(f"Edit {n}: each old cell can go in one new cell only.")
    seen.add(key)


def _set_cell(ctx: _Ctx, e: Elem, role: str, scope: str | None) -> str:
    """Header or data cell. Only Scope changes: the cell's spans and the headers it names stay, or its row would no
    longer cover the same columns as the others. In a table whose cells name their headers, the names follow: a header
    made a data cell is taken out of every cell's headers, and a cell made a header is added to the cells it heads."""
    obj = e.obj
    was = _role_of(ctx, obj)
    obj.S = Name("/" + role)
    attrs = obj.get("/A")
    items = list(attrs) if isinstance(attrs, Array) else ([attrs] if attrs is not None else [])
    table = next((a for a in items if isinstance(a, Dictionary) and a.get("/O") == Name.Table), None)
    if role == "TH":
        if table is None:
            table = Dictionary(O=Name.Table)
            items.append(table)
        table.Scope = Name("/" + scope)
    elif table is not None and "/Scope" in table:
        del table["/Scope"]
        if set(table.keys()) == {"/O"}:  # nothing left but the owner
            at = next(i for i, a in enumerate(items) if a is table)
            del items[at:at + 2 if at + 1 < len(items) and isinstance(items[at + 1], int) else at + 1]
    if not items:
        if "/A" in obj:
            del obj["/A"]
    else:
        obj.A = items[0] if len(items) == 1 else Array(items)
    relinked = _relink(ctx, obj, was, role, scope) if obj.is_indirect else 0
    return (f"element {e.index}: {role}" + (f" (scope {scope})" if role == "TH" else "")
            + (f"; the headers of {relinked} cell(s) follow" if relinked else ""))


# --- the headers a cell names (Headers and ID) ------------------------------------------------------------

def _table_of(ctx: _Ctx, cell: Dictionary) -> Dictionary | None:
    p, hops = cell.get("/P"), 0
    while isinstance(p, Dictionary) and hops < 4:
        if _role_of(ctx, p) == "Table":
            return p
        p, hops = p.get("/P"), hops + 1
    return None


def _grid(ctx: _Ctx, table: Dictionary) -> dict:
    """Where each cell of a table sits, from its rows and spans: objgen -> (row, col, row_span, col_span)."""
    rows = []
    for k in _items(table.get("/K")):
        role = _role_of(ctx, k) if is_elem(k) else None
        if role == "TR":
            rows.append(k)
        elif role in ("THead", "TBody", "TFoot"):
            rows += [r for r in _items(k.get("/K")) if is_elem(r) and _role_of(ctx, r) == "TR"]
    taken, out = set(), {}
    for r, tr in enumerate(rows):
        c = 0
        for cell in _items(tr.get("/K")):
            if not is_elem(cell) or _role_of(ctx, cell) not in ("TH", "TD") or not cell.is_indirect:
                continue
            while (r, c) in taken:
                c += 1
            a = table_attrs(cell)
            rs = max(1, min(100, int(a.get("/RowSpan", 1)))) if a is not None else 1
            cs = max(1, min(100, int(a.get("/ColSpan", 1)))) if a is not None else 1
            out[cell.objgen] = (r, c, rs, cs)
            taken |= {(r + i, c + j) for i in range(rs) for j in range(cs)}
            c += cs
    return out


def _cells_of(ctx: _Ctx, table: Dictionary) -> list[Dictionary]:
    """The table's own cells (not those of a table nested in one of them)."""
    out = []
    for x in walk(table):
        if _role_of(ctx, x) in ("TH", "TD"):
            t = _table_of(ctx, x)
            if t is not None and t.objgen == table.objgen:
                out.append(x)
    return out


def _set_named(cell: Dictionary, ids: list[bytes]) -> None:
    a = table_attrs(cell)
    if not ids:
        if a is not None and "/Headers" in a:
            del a["/Headers"]
        return
    if a is None:
        a = Dictionary(O=Name.Table)
        attrs = cell.get("/A")
        cell.A = a if attrs is None else Array((list(attrs) if isinstance(attrs, Array) else [attrs]) + [a])
    a.Headers = Array([String(i) for i in ids])


def _order(ids: list[bytes], cell_pos: tuple, pos_of_id: dict) -> list[bytes]:
    """Column headers (above the cell) top down, then row headers (to its left) left to right, then any others as
    they were."""
    def key(i):
        p = pos_of_id.get(i)
        if p is None:
            return (2, 0, 0)
        return (0, p[0], p[1]) if p[0] < cell_pos[0] else (1, p[1], p[0])
    return sorted(ids, key=key)


def _relink(ctx: _Ctx, cell: Dictionary, was: str | None, role: str, scope: str | None) -> int:
    """After a header/data change in a table whose cells name their headers: keep the names right. Returns how many
    cells' headers changed."""
    table = _table_of(ctx, cell)
    if table is None:
        return 0
    cells = _cells_of(ctx, table)
    if not any(headers_named(x) for x in cells):
        return 0  # the table uses Scope alone
    changed = 0
    if was == "TH" and role == "TD" and id_of(cell):
        gone = id_of(cell)
        for x in cells:
            named = headers_named(x)
            if gone in named:
                _set_named(x, [i for i in named if i != gone])
                changed += 1
        del cell["/ID"]
        ctx.ids_changed = True
    elif was == "TD" and role == "TH":
        grid = _grid(ctx, table)
        if cell.objgen not in grid:
            return 0
        r, c, rs, cs = grid[cell.objgen]
        mine = _give_id(ctx, cell, f"h{r}c{c}-{cell.objgen[0]}")
        pos_of_id = {id_of(x): grid[x.objgen] for x in cells if id_of(x) and x.objgen in grid}
        for x in cells:
            if x.objgen == cell.objgen or x.objgen not in grid:
                continue
            xr, xc, xrs, xcs = grid[x.objgen]
            below = scope in ("Column", "Both") and xr >= r + rs and set(range(xc, xc + xcs)) & set(range(c, c + cs))
            right = scope in ("Row", "Both") and xc >= c + cs and set(range(xr, xr + xrs)) & set(range(r, r + rs))
            named = headers_named(x)
            if (below or right) and mine not in named:
                _set_named(x, _order(named + [mine], (xr, xc), pos_of_id))
                changed += 1
    return changed


def _link_note(ctx: _Ctx, e: Elem, note: Elem | None, n: int) -> str:
    """The tag points at a note (/Ref, PDF 2.0), or at nothing when note is None. The note is given the ID a note
    needs if it has none. A PDF 2.0 note (FENote, in a PDF/UA-2 file) also names what points at it, as PDF/UA-2 asks
    both ways (8.2.5.14); a note the tag no longer points at stops naming it (#633)."""
    if not e.obj.is_indirect or _role_of(ctx, e.obj) in NOTES:
        raise EditError(f"Edit {n}: a note can't point at a note. Choose the mark or the text it's in.")
    if note is not None and (_role_of(ctx, note.obj) not in NOTES or not note.obj.is_indirect):
        raise EditError(f"Edit {n}: element {note.index} isn't a note. Change it to a Note first.")
    for old in _items(e.obj.get("/Ref")):
        if isinstance(old, Dictionary) and _role_of(ctx, old) == "FENote":
            _set_refs(old, [r for r in _items(old.get("/Ref")) if not _same(r, e.obj)])
    if note is None:
        if "/Ref" in e.obj:
            del e.obj["/Ref"]
        return f"element {e.index}: no longer points at a note"
    _give_id(ctx, note.obj, f"note-{note.obj.objgen[0]}")
    e.obj.Ref = Array([note.obj])
    if _role_of(ctx, note.obj) == "FENote":
        have = _items(note.obj.get("/Ref"))
        _set_refs(note.obj, have + ([] if any(_same(r, e.obj) for r in have) else [e.obj]))
    return f"element {e.index}: points at note {note.index}"


def _set_refs(obj, refs: list) -> None:
    """An element's /Ref as these elements, or none."""
    if refs:
        obj.Ref = Array(refs)
    elif "/Ref" in obj:
        del obj["/Ref"]


def _set_headers(ctx: _Ctx, e: Elem, heads: list[Elem], n: int) -> str:
    """The header cells a cell is read with: Headers naming them, each given an ID if it has none."""
    cell = e.obj
    if _role_of(ctx, cell) not in ("TH", "TD") or not cell.is_indirect:
        raise EditError(f"Edit {n}: only a table cell has headers.")
    table = _table_of(ctx, cell)
    for h in heads:
        if _role_of(ctx, h.obj) != "TH":
            raise EditError(f"Edit {n}: element {h.index} isn't a header cell, so it can't be one of the headers.")
        other = _table_of(ctx, h.obj) if h.obj.is_indirect else None
        if table is None or other is None or other.objgen != table.objgen:
            raise EditError(f"Edit {n}: element {h.index} is in another table.")
    grid = _grid(ctx, table) if table is not None else {}
    ids = []
    for h in heads:
        pos = grid.get(h.obj.objgen)
        ids.append(_give_id(ctx, h.obj, f"h{pos[0]}c{pos[1]}-{h.obj.objgen[0]}" if pos else f"h-{h.obj.objgen[0]}"))
    _set_named(cell, ids)
    names = ", ".join(str(h.index) for h in heads)
    return f"element {e.index}: read with header(s) {names}" if heads else f"element {e.index}: headers removed"


# --- the table editor (#370): a table rebuilt from its grid, or built from text tags ---------------------------

@dataclass
class _Slot:
    row: int
    col: int
    row_span: int
    col_span: int
    spec: dict


def _layout(rows: list[list[dict]], n: int) -> list[_Slot]:
    """Where each cell of the rows sits, as HTML lays out a table: the next free column of its row, then its spans.
    The cells must fill a rectangle: every row as wide as the others, nothing overlapping, no row span past the end."""
    taken: set[tuple[int, int]] = set()
    slots = []
    for r, row in enumerate(rows):
        c = 0
        for spec in row:
            while (r, c) in taken:
                c += 1
            rs, cs = spec.get("rows", 1), spec.get("cols", 1)
            cover = {(r + i, c + j) for i in range(rs) for j in range(cs)}
            if r + rs > len(rows) or cover & taken:
                raise EditError(f"Edit {n}: the cells don't fit together: a cell spans past the end of the table or "
                                "over another cell.")
            taken |= cover
            slots.append(_Slot(r, c, rs, cs, spec))
            c += cs
    width = 1 + max((c for _, c in taken), default=-1)
    if not taken or taken != {(r, c) for r in range(len(rows)) for c in range(width)}:
        raise EditError(f"Edit {n}: the rows don't line up: every row needs cells across the same number of columns.")
    return slots


def _cell_attrs(obj: Dictionary, role: str, scope: str | None, rows: int, cols: int) -> None:
    """A cell's Table attributes as the editor says: its scope (header cells), spans; its old Headers go (they're
    written again for the whole table). Other attribute owners stay."""
    attrs = obj.get("/A")
    items = [a for a in (list(attrs) if isinstance(attrs, Array) else [attrs] if attrs is not None else [])
             if not isinstance(a, int)]
    keep = [a for a in items if not (isinstance(a, Dictionary) and a.get("/O") == Name.Table)]
    table = Dictionary(O=Name.Table)
    if role == "TH":
        table.Scope = Name("/" + (scope or "Column"))
    if rows > 1:
        table.RowSpan = rows
    if cols > 1:
        table.ColSpan = cols
    items = keep + ([table] if len(table) > 1 else [])
    if not items:
        if "/A" in obj:
            del obj["/A"]
    else:
        obj.A = items[0] if len(items) == 1 else Array(items)


def _link_headers(ctx: _Ctx, slots: list[_Slot], objs: list[Dictionary], label: str) -> int:
    """Headers by name where Scope can't say it (table_headers), as the tagger writes them: the header rows and
    columns are the leading rows and columns of header cells."""
    def leading(axis: str) -> int:
        k = 0
        while any(getattr(s, axis) == k for s in slots) and all(
                s.spec["role"] == "TH" for s in slots if getattr(s, axis) <= k):
            k += 1
        return k
    header_rows, header_cols = leading("row"), leading("col")
    ctx.ids_changed = True  # cells may have gone with their IDs, and header cells may get some
    if not is_complex(slots, header_rows, header_cols):
        return 0
    at = {(s.row, s.col): o for s, o in zip(slots, objs)}
    links = header_links(slots, header_rows, header_cols)
    for (r, c), heads in links.items():
        ids = [_give_id(ctx, at[h], f"{label}-r{h[0]}c{h[1]}") for h in heads]
        cell = at[(r, c)]
        a = table_attrs(cell)
        if a is None:
            a, old = Dictionary(O=Name.Table), cell.get("/A")
            cell.A = a if old is None else Array((list(old) if isinstance(old, Array) else [old]) + [a])
        a.Headers = Array([String(i) for i in ids])
    return len(links)


def _table_rows(ctx: _Ctx, table: Dictionary) -> list[Dictionary]:
    rows = []
    for k in _items(table.get("/K")):
        role = _role_of(ctx, k) if is_elem(k) else None
        if role == "TR":
            rows.append(k)
        elif role in ("THead", "TBody", "TFoot"):
            rows += [r for r in _items(k.get("/K")) if is_elem(r) and _role_of(ctx, r) == "TR"]
    return rows


def _new_elem(ctx: _Ctx, role: str, parent: Dictionary, like: Dictionary | None = None) -> Dictionary:
    return new_elem(ctx.pdf, role, parent, like.Pg if like is not None and "/Pg" in like else None)


def _rebuild_table(ctx: _Ctx, e: Elem, op: dict, t: dict, n: int) -> str:
    if e.role != "Table" or not e.obj.is_indirect:
        raise EditError(f"Edit {n}: element {e.index} is a {e.role}, not a table.")
    table = e.obj
    # A cell whose words belong in several cells is cut first, from its last cut back (each cut leaves it shorter):
    # its parts are new cells right after it, named by part number
    parts: dict[int, list[Elem]] = {}
    for idx, cell in t["cuts"].items():
        if cell.role not in ("TH", "TD") or _table_of(ctx, cell.obj) is None or not _same(_table_of(ctx, cell.obj), table):
            raise EditError(f"Edit {n}: a cell cut isn't one of this table's cells. Reload and try again.")
        made = []
        for cut in sorted(op["cuts"][str(idx)], key=lambda c: (c["at"], c.get("char", 0)), reverse=True):
            if cut.get("char"):
                _split_inside(ctx, cell, cut["at"], cut["char"], n)
            else:
                _split(ctx, cell, cut["at"], n)
            made.append(Elem(index=len(ctx.elems), obj=ctx.made, role=cell.role, raw_role=cell.raw_role, page=cell.page,
                             parent=cell.parent))
            ctx.elems.append(made[-1])
        parts[idx] = [cell] + made[::-1]

    def resolve(x):
        if isinstance(x, tuple):
            got = parts.get(x[0].index, [])
            if x[1] >= len(got):
                raise EditError(f"Edit {n}: a cell's text was cut into fewer parts than the table names.")
            return got[x[1]]
        return x
    t = {**t, "cells": [[[resolve(x) for x in cell] for cell in row] for row in t["cells"]],
         "out": [[resolve(x) for x in para] for para in t["out"]]}
    own = {c.objgen for c in _cells_of(ctx, table)}
    listed = [x for row in t["cells"] for cell in row for x in cell] + [x for para in t["out"] for x in para]
    if any(x.obj.objgen not in own for x in listed if x.obj.is_indirect) or any(not x.obj.is_indirect for x in listed):
        raise EditError(f"Edit {n}: a cell named isn't one of this table's cells. Reload and try again.")
    missing = own - {x.obj.objgen for x in listed}
    if missing:
        raise EditError(f"Edit {n}: {len(missing)} of the table's cells aren't in the new table; their content would "
                        "be lost. Reload and try again.")
    slots = _layout(op["rows"], n)
    old_rows = _table_rows(ctx, table)
    trs, objs, merged = [], [], 0
    specs = iter(t["cells"])
    for r, row in enumerate(op["rows"]):
        tr = old_rows[r] if r < len(old_rows) else _new_elem(ctx, "TR", table, table)
        tr.P = table
        kids = []
        for spec, olds in zip(row, next(specs)):
            if olds:
                base = olds[0].obj
                content = _items(base.get("/K"))
                for o in olds[1:]:
                    content += _reparent_items(ctx, o, _items(o.obj.get("/K")), base)
                    o.obj.K = Array([])
                    merged += 1
                base.K = Array(content)
            else:
                base = _new_elem(ctx, spec["role"], tr, table)
            base.S = Name("/" + spec["role"])
            base.P = tr
            if "text" in spec:  # what it's read as, set in the table editor (empty: its drawn text)
                if spec["text"].strip():
                    base.ActualText = String(spec["text"].strip())
                elif "/ActualText" in base:
                    del base["/ActualText"]
            _cell_attrs(base, spec["role"], spec.get("scope"), spec.get("rows", 1), spec.get("cols", 1))
            kids.append(base)
            objs.append(base)
        tr.K = Array(kids)
        trs.append(tr)
    for s, o in zip(slots, objs):
        a = table_attrs(o)
        if a is not None and "/Headers" in a:
            del a["/Headers"]
    linked = _link_headers(ctx, slots, objs, f"t{e.index}")
    old_caption = next((k for k in _items(table.get("/K")) if is_elem(k) and _role_of(ctx, k) == "Caption"), None)
    caption = _table_caption(ctx, e, op, t.get("caption"), n)
    table.K = Array(([caption] if caption is not None else []) + trs)
    # Cells that aren't the table's (a paragraph set beside it, read as a column): one paragraph each, after it
    outside = []
    for spec, cells in zip(op.get("out") or [], t["out"]):
        parent = _parent_obj(ctx, e)
        para = _new_elem(ctx, spec.get("role", "P"), parent, table)
        content = []
        for o in cells:
            content += _reparent_items(ctx, o, _items(o.obj.get("/K")), para)
            o.obj.K = Array([])
        para.K = Array(content)
        outside.append(para)
    if outside:
        parent = _parent_obj(ctx, e)
        siblings = _items(parent.get("/K"))
        pos = next((i for i, k in enumerate(siblings) if _same(k, table)), len(siblings) - 1)
        parent.K = Array(siblings[:pos + 1] + outside + siblings[pos + 1:])
    # The tags as the table now has them, in the order the tag manager shows them (refs.py): an old caption put back
    # as a paragraph, the table, its caption, each row and its cells, then the paragraphs taken out after it
    ctx.new_tags = ([old_caption] if old_caption is not None and op.get("caption", 0) is None else []) + [table] + (
        [caption] if caption is not None else [])
    cells = iter(objs)
    for tr, row in zip(trs, op["rows"]):
        ctx.new_tags += [tr] + [next(cells) for _ in row]
    ctx.new_tags += outside
    width = 1 + max(s.col + s.col_span - 1 for s in slots)
    return (f"element {e.index} (Table): {len(trs)} row(s) by {width} column(s)"
            + (f", {merged} cell(s) merged into others" if merged else "")
            + (f", {sum(len(v) - 1 for v in parts.values())} cut(s) in cells' text" if parts else "")
            + (f", {len(outside)} paragraph(s) taken out after it" if outside else "")
            + (f"; {linked} cell(s) name their headers" if linked else ""))


def _table_caption(ctx: _Ctx, e: Elem, op: dict, chosen: Elem | None, n: int) -> Dictionary | None:
    """The table's caption after the edit: unchanged when the edit doesn't say; a tag made the caption (moved into
    the table, first); or none, the old one put back as a paragraph just before the table."""
    table = e.obj
    old = next((k for k in _items(table.get("/K")) if is_elem(k) and _role_of(ctx, k) == "Caption"), None)
    if "caption" not in op:
        return old
    if chosen is not None and old is not None and _same(chosen.obj, old):
        return old
    if old is not None:  # out, as a paragraph before the table
        parent = _parent_obj(ctx, e)
        old.S, old.P = Name.P, parent
        siblings = _items(parent.get("/K"))
        pos = next((i for i, k in enumerate(siblings) if _same(k, table)), len(siblings))
        parent.K = Array(siblings[:pos] + [old] + siblings[pos:])
    if chosen is None:
        return None
    if chosen.index == e.index or e.index in _ancestors(ctx, chosen) or chosen.role not in LIST_ITEM_FROM | {"Caption"}:
        raise EditError(f"Edit {n}: a table's caption is a paragraph or heading outside the table.")
    if not chosen.obj.is_indirect:
        raise EditError(f"Edit {n}: that tag can't be moved into the table.")
    _detach(ctx, chosen)
    chosen.obj.S, chosen.obj.P = Name.Caption, table
    return chosen.obj


def _build_table(ctx: _Ctx, e: Elem, others: list[Elem], op: dict, n: int) -> str:
    """Text tags made a table: each cell takes the /K items it names; every item goes somewhere. A figure can be one of
    them, for a table drawn as a picture (a grid of ruled lines): its alt text described the picture, and goes."""
    tags = [e] + others
    for x in tags:
        if not x.obj.is_indirect or x.role not in LIST_ITEM_FROM | {"Figure"}:
            raise EditError(f"Edit {n}: a {x.role} can't be made into a table. Select paragraphs, headings or a "
                            "figure.")
        if x.role == "Figure" and "/Alt" in x.obj:
            del x.obj["/Alt"]
        if any(k in x.obj for k in ("/ActualText", "/Alt")):
            raise EditError(f"Edit {n}: element {x.index} has read-aloud or alt text that stands for all of it. "
                            "Remove that first.")
        inside = _ancestors(ctx, x)
        if any(y.index in inside for y in tags):
            raise EditError(f"Edit {n}: a tag can't be made into a table with a tag that contains it.")
    parent = _parent_obj(ctx, e)
    if not _at_root(ctx, parent):
        v = check_move("Table", _role_of(ctx, parent), _role_of(ctx, parent), True)
        if not v.ok:
            raise EditError(f"Edit {n}: a table can't go here. {v.reason}")
    items = {(x.index, k): (x, item) for x in tags for k, item in enumerate(_items(x.obj.get("/K")))}
    named = [(ref.get("el", e.index), ref["at"]) for row in op["rows"] for cell in row for ref in cell["parts"]]
    if set(named) != set(items) or len(named) != len(items):
        raise EditError(f"Edit {n}: every piece of the tags has to go in exactly one cell "
                        f"({len(set(items) - set(named))} left out). Reload and try again.")
    slots = _layout(op["rows"], n)
    table = _new_elem(ctx, "Table", parent, e.obj)
    trs, objs = [], []
    ctx.new_tags = [table]  # the table, then each row and its cells (refs.py)
    for row in op["rows"]:
        tr = _new_elem(ctx, "TR", table, e.obj)
        ctx.new_tags.append(tr)
        kids = []
        for spec in row:
            cell = _new_elem(ctx, spec["role"], tr, e.obj)
            content: list = []
            for ref in spec["parts"]:
                owner, item = items[(ref.get("el", e.index), ref["at"])]
                content += _reparent_items(ctx, owner, [item], cell)
            cell.K = Array(content)
            for key in ("/Lang",):
                if key in e.obj:
                    cell[key] = e.obj[key]
            _cell_attrs(cell, spec["role"], spec.get("scope"), spec.get("rows", 1), spec.get("cols", 1))
            kids.append(cell)
            objs.append(cell)
            ctx.new_tags.append(cell)
        tr.K = Array(kids)
        trs.append(tr)
    table.K = Array(trs)
    linked = _link_headers(ctx, slots, objs, f"t{e.index}")
    for x in tags:
        x.obj.K = Array([])
    for o in others:
        _detach(ctx, o)
    siblings = _items(parent.get("/K"))
    pos = next((i for i, k in enumerate(siblings) if _same(k, e.obj)), None)
    if pos is None:
        raise EditError(f"Edit {n}: the tag wasn't found under its parent.")
    parent.K = Array(siblings[:pos] + [table] + siblings[pos + 1:])
    width = 1 + max(s.col + s.col_span - 1 for s in slots)
    return (f"element {e.index}: made into a table of {len(trs)} row(s) by {width} column(s)"
            + (f"; {linked} cell(s) name their headers" if linked else ""))


def _check_cell(op: dict, n: int) -> None:
    if op.get("role") not in ("TH", "TD"):
        raise EditError(f"Edit {n}: a cell must be TH or TD.")
    if op["role"] == "TH" and op.get("scope") not in SCOPES:
        raise EditError(f"Edit {n}: a header cell needs a scope of Row, Column or Both.")


def _check_headers(op: dict, n: int) -> None:
    heads = op.get("headers")
    if (not isinstance(heads, list) or len(heads) > MAX_HEADERS or len(set(heads)) != len(heads)
            or not all(isinstance(h, int) and not isinstance(h, bool) and h >= 0 for h in heads)
            or op["element"] in heads):
        raise EditError(f"Edit {n}: a cell's headers are a list of other cells (at most {MAX_HEADERS}).")


def _check_note(op: dict, n: int) -> None:
    note = op.get("note")
    if note is not None and (not isinstance(note, int) or isinstance(note, bool) or note < 0 or note == op["element"]):
        raise EditError(f"Edit {n}: a link needs the note to point at (or null to remove it).")


def _table_targets(ctx: _Ctx, op: dict, n: int) -> dict:
    """rebuild_table: the old cells each new cell takes (a cut cell's part as (cell, part)), the cells cut, the
    paragraphs taken out, and the caption chosen."""
    ref = lambda r: (_elem(ctx, r[0], None, n), r[1]) if isinstance(r, list) else _elem(ctx, r, None, n)  # noqa: E731
    return {"cells": [[[ref(i) for i in cell["from"]] for cell in row] for row in op["rows"]],
            "cuts": {int(k): _elem(ctx, int(k), None, n) for k in (op.get("cuts") or {})},
            "out": [[ref(i) for i in para["from"]] for para in op.get("out") or []],
            "caption": _elem(ctx, op["caption"], None, n) if op.get("caption") is not None else None}


register("set_cell", lambda ctx, op, e, t, n: _set_cell(ctx, e, op["role"], op.get("scope")), _check_cell)
register("set_headers", lambda ctx, op, e, t, n: _set_headers(ctx, e, t, n), _check_headers,
         lambda ctx, op, n: [_elem(ctx, h, None, n) for h in op["headers"]])
register("link_note", lambda ctx, op, e, t, n: _link_note(ctx, e, t, n), _check_note,
         lambda ctx, op, n: _elem(ctx, op["note"], None, n) if op.get("note") is not None else None)
register("rebuild_table", lambda ctx, op, e, t, n: _rebuild_table(ctx, e, op, t, n), _check_table_rows, _table_targets)
register("build_table", lambda ctx, op, e, t, n: _build_table(ctx, e, t, op, n), _check_table_rows, _absorbed)
