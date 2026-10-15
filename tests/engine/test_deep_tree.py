"""A structure tree nested thousands of elements deep. No producer makes one; a broken or hostile file does, and the
tree walk is recursive, so the job used to fail with a traceback instead of rebuilding the tags."""
import pikepdf
from pikepdf import Array, Dictionary, Name

from app.pipeline.orchestrator import prepare_analysis
from app.tagging.tagger import tag_document
from app.verification.integrity import check_integrity

DEPTH = 5000


def _deep_tree(path):
    pdf = pikepdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica,
                                        Encoding=Name.WinAnsiEncoding))
    page = pdf.make_indirect(Dictionary(
        Type=Name.Page, MediaBox=Array([0, 0, 612, 792]), Resources=Dictionary(Font=Dictionary(F1=font)),
        Contents=pdf.make_stream(b"/P <</MCID 0>> BDC BT /F1 12 Tf 72 700 Td (Deep down) Tj ET EMC")))
    pdf.Root.Pages = pdf.make_indirect(Dictionary(Type=Name.Pages, Kids=Array([page]), Count=1))
    page.Parent = pdf.Root.Pages
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    doc = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Document, P=root))
    parent = doc
    for _ in range(DEPTH):
        div = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Div, P=parent, Pg=page))
        parent.K = Array([div])
        parent = div
    leaf = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.P, P=parent, Pg=page, K=0))
    parent.K = Array([leaf])
    root.K = Array([doc])
    root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array([0, Array([leaf])])))
    page.StructParents = 0
    pdf.Root.StructTreeRoot = root
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    pdf.save(path)


def test_a_tree_nested_too_deeply_is_rebuilt(tmp_path):
    src, out = tmp_path / "in.pdf", tmp_path / "out.pdf"
    _deep_tree(src)
    result = tag_document(src, out, prepare_analysis(src), title="Deep", language="en-US", pdfua_part=1)
    assert result.existing_tags["rebuilt_tree"] and "too deeply" in result.existing_tags["reason"]
    with pikepdf.open(out) as pdf:
        findings, index = check_integrity(pdf)
    assert [f for f in findings if f.severity == "error"] == []
    assert any(e.role == "P" for e in index.elements)  # the text has a tag in the rebuilt tree
