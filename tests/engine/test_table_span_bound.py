"""Kept tags: a table cell whose ColSpan (or RowSpan) is absurd. Every row is padded to the widest, so a cell
claiming a span of thousands used to make the tagger create that many empty cells in every other row."""
import pikepdf
from pikepdf import Array, Dictionary, Name

from app.pipeline.orchestrator import prepare_analysis
from app.tagging.tagger import tag_document
from app.verification.integrity import check_integrity

ABSURD = 5000


def _table_pdf(path, col_span: int):
    pdf = pikepdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica,
                                        Encoding=Name.WinAnsiEncoding))
    cells = [("Name", 72, 700), ("Dose", 200, 700), ("Aspirin", 72, 670), ("100 mg", 200, 670)]
    content = [f"/TD <</MCID {i}>> BDC BT /F1 12 Tf {x} {y} Td ({t}) Tj ET EMC".encode() for i, (t, x, y) in enumerate(cells)]
    page = pdf.make_indirect(Dictionary(
        Type=Name.Page, MediaBox=Array([0, 0, 612, 792]), Resources=Dictionary(Font=Dictionary(F1=font)),
        Contents=pdf.make_stream(b"\n".join(content))))
    pdf.Root.Pages = pdf.make_indirect(Dictionary(Type=Name.Pages, Kids=Array([page]), Count=1))
    page.Parent = pdf.Root.Pages
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    doc = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Document, P=root))
    table = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Table, P=doc, Pg=page))

    def cell(parent, mcid, span=None):
        d = Dictionary(Type=Name.StructElem, S=Name.TD, P=parent, Pg=page, K=mcid)
        if span:
            d.A = Dictionary(O=Name.Table, ColSpan=span)
        return pdf.make_indirect(d)
    rows = [pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.TR, P=table, Pg=page)) for _ in range(2)]
    rows[0].K = Array([cell(rows[0], 0, col_span), cell(rows[0], 1)])
    rows[1].K = Array([cell(rows[1], 2), cell(rows[1], 3)])
    table.K = Array(rows)
    doc.K = Array([table])
    root.K = Array([doc])
    root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array([0, Array([rows[0].K[0], rows[0].K[1], rows[1].K[0], rows[1].K[1]])])))
    page.StructParents = 0
    pdf.Root.StructTreeRoot = root
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    pdf.save(path)


def _cells_per_row(path):
    with pikepdf.open(path) as pdf:
        findings, index = check_integrity(pdf)
        assert [f for f in findings if f.severity == "error"] == []
        rows = [e for e in index.elements if e.role == "TR"]
        return [len(e.kids) for e in rows]


def test_an_absurd_column_span_is_not_padded_out(tmp_path):
    src, out = tmp_path / "in.pdf", tmp_path / "out.pdf"
    _table_pdf(src, ABSURD)
    result = tag_document(src, out, prepare_analysis(src), title="T", language="en-US", pdfua_part=1)
    assert _cells_per_row(out) == [2, 2]  # the span was ignored: nothing to pad
    assert result.existing_tags["present"] and not result.existing_tags["rebuilt_tree"]


def test_a_real_column_span_is_still_padded(tmp_path):
    src, out = tmp_path / "in.pdf", tmp_path / "out.pdf"
    _table_pdf(src, 3)
    tag_document(src, out, prepare_analysis(src), title="T", language="en-US", pdfua_part=1)
    assert _cells_per_row(out) == [2, 4]  # row 1 spans 4 columns; row 2 got two empty cells
