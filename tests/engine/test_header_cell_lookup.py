"""Kept tags: a header cell's table is found by walking up its parents. The walk used to scan every element at
each step, so a document with thousands of header cells (a test bank with a table per question) spent its time
there; it must stay fast as the tree grows."""
import time

import pikepdf
from pikepdf import Array, Dictionary, Name

from app.tagging.existing_tags import ExistingTree

TABLES, CELLS = 600, 12


def _many_tables():
    """One page; TABLES tables of one row with CELLS header cells each, and a paragraph between tables."""
    pdf = pikepdf.new()
    page = pdf.make_indirect(Dictionary(Type=Name.Page, MediaBox=Array([0, 0, 612, 792]),
                                        Contents=pdf.make_stream(b"")))
    pdf.Root.Pages = pdf.make_indirect(Dictionary(Type=Name.Pages, Kids=Array([page]), Count=1))
    page.Parent = pdf.Root.Pages
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    doc = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Document, P=root))
    kids, mcid = [], 0
    for _ in range(TABLES):
        table = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Table, P=doc, Pg=page))
        row = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.TR, P=table, Pg=page))
        cells = []
        for _ in range(CELLS):
            cells.append(pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.TH, P=row, Pg=page, K=mcid)))
            mcid += 1
        row.K = Array(cells)
        table.K = Array([row])
        kids.append(table)
        kids.append(pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.P, P=doc, Pg=page, K=mcid)))
        mcid += 1
    doc.K = Array(kids)
    root.K = Array([doc])
    pdf.Root.StructTreeRoot = root
    return pdf, page.objgen, mcid


def test_thousands_of_header_cells_are_repaired_in_seconds():
    pdf, page, mcids = _many_tables()
    tree = ExistingTree(pdf, {page: set(range(mcids))})
    assert tree.inspect().usable
    started = time.monotonic()
    tree.repair(lambda e: None)
    took = time.monotonic() - started
    scoped = sum(1 for e in tree.elements if e.role == "TH" and any("TH Scope" in r for r in e.reasons))
    assert scoped == TABLES * CELLS  # every header cell got its Scope, as before
    assert took < 10.0, f"repairing {TABLES * CELLS} header cells took {took:.0f} s"
