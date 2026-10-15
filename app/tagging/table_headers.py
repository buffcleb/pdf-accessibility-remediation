"""Which header cells each cell of a table is read with, for tables that Scope alone can't describe.

Scope on a header cell ("this heads its column") is enough for a table with one header row and at most one header
column. A table with two levels of column headings ("2025" over "Q1 Q2"), several header columns, or a header that
spans columns needs each cell linked to its headers by name: an ID on every header cell, and Headers on every cell
listing the IDs that apply (WCAG 1.3.1, technique PDF6; the PDF equivalent of HTML's headers attribute).

The headers of a cell are the ones the HTML table model gives it: the header cells above it, in the header rows, that
share one of its columns, and the header cells to its left, in the header columns, that share one of its rows. A
header cell has headers too (a quarter's heading is under its year's), found the same way. The corner, where the
header rows and columns meet, heads the row headers below it ("Region" over the region names).
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

import pikepdf
from pikepdf import Array, Dictionary, String


class Cell(Protocol):
    row: int
    col: int
    row_span: int
    col_span: int


def is_complex(cells: Iterable[Cell], header_rows: int, header_cols: int) -> bool:
    """True when the table needs Headers: more than one header row or column, or a header cell that spans some of
    the columns or more than one row. A header across the whole width (a title row) heads every cell, which its
    Scope already says."""
    if header_rows > 1 or header_cols > 1:
        return True
    cells = list(cells)
    width = max((c.col + c.col_span for c in cells), default=0)
    return any((c.row < header_rows or c.col < header_cols)
               and (c.row_span > 1 or 1 < c.col_span < width) for c in cells)


def header_links(cells: list[Cell], header_rows: int, header_cols: int) -> dict[tuple[int, int], list[tuple[int, int]]]:
    """For each cell, by (row, col) where it starts, the header cells it's read with, by where they start: column
    headers first, top down, then row headers, left to right. Cells with none are left out."""
    heads = [c for c in cells if c.row < header_rows or c.col < header_cols]
    out: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for c in cells:
        rows = range(c.row, c.row + c.row_span)
        cols = range(c.col, c.col + c.col_span)
        above = [h for h in heads if h.row < header_rows and h.row + h.row_span <= c.row
                 and any(k in cols for k in range(h.col, h.col + h.col_span))]
        # The corner (header rows and header columns both) heads the row headers under it, not the column headings
        # beside it: a cell in the header rows takes no row headers from the corner
        left = [h for h in heads if h.col < header_cols and h.col + h.col_span <= c.col
                and any(r in rows for r in range(h.row, h.row + h.row_span))
                and not (c.row < header_rows and h.row < header_rows)]
        found = sorted(above, key=lambda h: (h.row, h.col)) + sorted(left, key=lambda h: (h.col, h.row))
        if found:
            out[(c.row, c.col)] = [(h.row, h.col) for h in found]
    return out


def id_of(e) -> bytes:
    """An element's ID as bytes (b"" when it has none, or something other than a string)."""
    v = e.get("/ID") if isinstance(e, Dictionary) else None
    return bytes(v) if isinstance(v, String) else b""


def write_id_tree(pdf: pikepdf.Pdf, root: Dictionary, elems: Iterable) -> None:
    """The IDTree PDF requires once any element has an ID: ID -> element, for every ID among elems (the whole tree),
    keys in byte order. One leaf holds them all, as tables add a few hundred at most. With no IDs left, an IDTree
    is removed: it would name elements that no longer have them."""
    found: dict[bytes, pikepdf.Object] = {}
    for e in elems:
        if id_of(e) and e.is_indirect:
            found.setdefault(id_of(e), e)
    if not found:
        if "/IDTree" in root:
            del root["/IDTree"]
        return
    names = Array()
    for key in sorted(found):
        names.append(String(key))
        names.append(found[key])
    root.IDTree = pdf.make_indirect(Dictionary(Names=names))


def table_attrs(obj) -> Dictionary | None:
    """A cell's Table attribute dictionary (/A may be one dictionary or an array of them with revision numbers)."""
    attrs = obj.get("/A")
    for a in (list(attrs) if isinstance(attrs, Array) else [attrs]):
        if isinstance(a, Dictionary) and a.get("/O") == "/Table":
            return a
    return None


def headers_named(obj) -> list[bytes]:
    """The IDs a cell's Headers names, in order."""
    a = table_attrs(obj)
    h = a.get("/Headers") if a is not None else None
    return [bytes(x) for x in (list(h) if isinstance(h, Array) else []) if isinstance(x, String)]
