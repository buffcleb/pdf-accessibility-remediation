"""Kept tags: a table whose first column has a cell merged over two rows, written (as Word and Acrobat do) as one TD in
the first of them and nothing in the second, with no RowSpan. Padding the short row at the end moved its cells one
column left (#557); the table drawn on the page says where they belong."""
import pikepdf
from pikepdf import Array, Dictionary, Name

from app.pipeline.orchestrator import prepare_analysis
from app.tagging.tagger import tag_document
from app.verification.integrity import check_integrity

COLS = [60, 200, 330, 460]
ROWS = [730, 700, 670, 640]  # top of the header row, then each row's bottom


def _table_pdf(path, drawn=True, merged=True):
    """Header (Group, Item, Note), then "Fruit | Apple | Red" and "Pear | Green" under it. With `merged`, the ruling
    leaves the first column open between the two body rows (Fruit covers both); without it, that column has its own
    empty box in the last row. With `drawn` False there are no lines at all."""
    pdf = pikepdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica,
                                        Encoding=Name.WinAnsiEncoding))
    rows = [[("Group", 0), ("Item", 1), ("Note", 2)], [("Fruit", 0), ("Apple", 1), ("Red", 2)],
            [("Pear", 1), ("Green", 2)]]
    content, mcid, placed = [], 0, []
    for r, row in enumerate(rows):
        ids = []
        for text, col in row:
            content.append(f"/TD <</MCID {mcid}>> BDC BT /F1 10 Tf {COLS[col] + 6} {ROWS[r + 1] + 10} Td ({text}) Tj "
                           "ET EMC")
            ids.append(mcid)
            mcid += 1
        placed.append(ids)
    if drawn:
        lines = [f"{COLS[0]} {ROWS[3]} {COLS[3] - COLS[0]} {ROWS[0] - ROWS[3]} re S"]
        lines += [f"{x} {ROWS[0]} m {x} {ROWS[3]} l S" for x in COLS[1:3]]
        lines.append(f"{COLS[0]} {ROWS[1]} m {COLS[3]} {ROWS[1]} l S")
        lines.append(f"{COLS[1] if merged else COLS[0]} {ROWS[2]} m {COLS[3]} {ROWS[2]} l S")
        content.append("/Artifact BMC 0 G 1 w " + " ".join(lines) + " EMC")
    page = pdf.make_indirect(Dictionary(
        Type=Name.Page, MediaBox=Array([0, 0, 612, 792]), Resources=Dictionary(Font=Dictionary(F1=font)),
        Contents=pdf.make_stream("\n".join(content).encode())))
    pdf.Root.Pages = pdf.make_indirect(Dictionary(Type=Name.Pages, Kids=Array([page]), Count=1))
    page.Parent = pdf.Root.Pages
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    doc = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Document, P=root))
    table = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Table, P=doc, Pg=page))
    trs, tds = [], []
    for ids in placed:
        tr = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.TR, P=table, Pg=page))
        cells = [pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.TD, P=tr, Pg=page, K=i)) for i in ids]
        tr.K = Array(cells)
        trs.append(tr)
        tds += cells
    table.K = Array(trs)
    doc.K, root.K = Array([table]), Array([doc])
    root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array([0, Array(tds)])))
    page.StructParents = 0
    pdf.Root.StructTreeRoot = root
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    pdf.save(path)


def _rows(path):
    """Each row as (text or "" for an empty cell, RowSpan) per cell, after checking the tree."""
    with pikepdf.open(path) as pdf:
        findings, _ = check_integrity(pdf)
        assert [f for f in findings if f.severity == "error"] == []
        page = pdf.pages[0]
        texts = {}
        for operands, op in pikepdf.parse_content_stream(page):
            if str(op) == "BDC" and isinstance(operands[1], Dictionary) and "/MCID" in operands[1]:
                current = int(operands[1].MCID)
            elif str(op) == "Tj":
                texts[current] = bytes(operands[0]).decode("latin-1")

        def find(e, role):
            if not isinstance(e, Dictionary):
                return []
            if e.get("/S") == Name("/" + role):
                return [e]
            kids = e.get("/K")
            return [x for k in (kids if isinstance(kids, Array) else [kids]) for x in find(k, role)]
        out = []
        for tr in find(pdf.Root.StructTreeRoot, "TR"):
            row = []
            for td in find(tr, "TD") + find(tr, "TH"):
                k = td.get("/K")
                ids = [int(x) for x in (k if isinstance(k, Array) else [k]) if x is not None and not isinstance(x, Dictionary)]
                attrs = td.get("/A")
                span = int(attrs.RowSpan) if isinstance(attrs, Dictionary) and "/RowSpan" in attrs else 1
                row.append(("".join(texts.get(i, "") for i in ids), span))
            out.append(row)
        return out


def _run(tmp_path, **kw):
    src, out = tmp_path / "in.pdf", tmp_path / "out.pdf"
    _table_pdf(src, **kw)
    tag_document(src, out, prepare_analysis(src), title="Fruit", language="en-US", pdfua_part=1)
    return _rows(out)


def test_a_cell_merged_down_the_first_column_gets_its_row_span(tmp_path):
    rows = _run(tmp_path)
    assert [t for t, _ in rows[1]] == ["Fruit", "Apple", "Red"] and rows[1][0][1] == 2
    assert [t for t, _ in rows[2]] == ["Pear", "Green"]  # in columns 2 and 3: Fruit's span covers the first


def test_a_blank_box_in_the_drawing_gets_an_empty_cell_in_its_place(tmp_path):
    rows = _run(tmp_path, merged=False)
    assert all(span == 1 for row in rows for _, span in row)
    assert [t for t, _ in rows[2]] == ["", "Pear", "Green"]


def test_with_no_table_drawn_the_short_row_is_padded_at_the_end(tmp_path):
    rows = _run(tmp_path, drawn=False)
    assert [t for t, _ in rows[2]] == ["Pear", "Green", ""]


def _titled_table_pdf(path):
    """A two-column ruled table under a title row drawn across both columns, the title tagged TH alone in its row."""
    pdf = pikepdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica,
                                        Encoding=Name.WinAnsiEncoding))
    cols, rows = [60, 260, 460], [730, 700, 670, 640]
    texts = [[("Table 1. Fruit and colour", 0, "TH")], [("Apple", 0, "TD"), ("Red", 1, "TD")],
             [("Pear", 0, "TD"), ("Green", 1, "TD")]]
    content, placed, mcid = [], [], 0
    for r, row in enumerate(texts):
        ids = []
        for text, col, role in row:
            content.append(f"/{role} <</MCID {mcid}>> BDC BT /F1 10 Tf {cols[col] + 6} {rows[r + 1] + 10} Td ({text}) "
                           "Tj ET EMC")
            ids.append((mcid, role))
            mcid += 1
        placed.append(ids)
    lines = [f"{cols[0]} {rows[3]} {cols[2] - cols[0]} {rows[0] - rows[3]} re S",
             f"{cols[1]} {rows[1]} m {cols[1]} {rows[3]} l S"]
    lines += [f"{cols[0]} {y} m {cols[2]} {y} l S" for y in rows[1:3]]
    content.append("/Artifact BMC 0 G 1 w " + " ".join(lines) + " EMC")
    page = pdf.make_indirect(Dictionary(
        Type=Name.Page, MediaBox=Array([0, 0, 612, 792]), Resources=Dictionary(Font=Dictionary(F1=font)),
        Contents=pdf.make_stream("\n".join(content).encode())))
    pdf.Root.Pages = pdf.make_indirect(Dictionary(Type=Name.Pages, Kids=Array([page]), Count=1))
    page.Parent = pdf.Root.Pages
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    doc = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Document, P=root))
    table = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Table, P=doc, Pg=page))
    trs, cells = [], []
    for ids in placed:
        tr = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.TR, P=table, Pg=page))
        row = [pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name("/" + role), P=tr, Pg=page, K=i))
               for i, role in ids]
        tr.K = Array(row)
        trs.append(tr)
        cells += row
    table.K = Array(trs)
    doc.K, root.K = Array([table]), Array([doc])
    root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array([0, Array(cells)])))
    page.StructParents = 0
    pdf.Root.StructTreeRoot = root
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    pdf.save(path)


def test_a_spanning_header_keeps_its_span_and_scope_in_one_table_dictionary(tmp_path):
    """PAC reads one Table attribute dictionary per cell: the Scope written in a second one, beside the ColSpan, left
    the title's header cell with "no associated subcells" (6 errors on the 332-page guideline after #559)."""
    src, out = tmp_path / "in.pdf", tmp_path / "out.pdf"
    _titled_table_pdf(src)
    tag_document(src, out, prepare_analysis(src), title="Fruit", language="en-US", pdfua_part=1)
    with pikepdf.open(out) as pdf:
        findings, _ = check_integrity(pdf)
        assert [f for f in findings if f.severity == "error"] == []
        ths = [o for o in pdf.objects if isinstance(o, Dictionary) and o.get("/S") == Name.TH]
        assert len(ths) == 1
        attrs = ths[0].get("/A")
        tables = [a for a in (list(attrs) if isinstance(attrs, Array) else [attrs])
                  if isinstance(a, Dictionary) and a.get("/O") == Name.Table]
        assert len(tables) == 1
        assert int(tables[0].ColSpan) == 2 and "/Scope" in tables[0]
