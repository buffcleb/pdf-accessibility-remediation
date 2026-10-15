"""Links written straight into a page's annotation list, not as objects of their own (a lab manual made with an
online converter): a tag can only point at an object, so they were left untagged and the file failed PDF/UA
7.18.1 and 7.18.5."""
import pikepdf
from pikepdf import Array, Dictionary, Name, String

from app.pipeline.orchestrator import prepare_analysis
from app.tagging.tagger import tag_document
from app.verification.integrity import check_integrity
from app.verification.tagmap import build_tagmap


def _doc(path):
    pdf = pikepdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica,
                                        Encoding=Name.WinAnsiEncoding))
    body = b"BT /F1 11 Tf 72 700 Td (Read more at the project page, and at the second site.) Tj ET"
    link = lambda rect, uri: Dictionary(Type=Name.Annot, Subtype=Name.Link, Rect=Array(rect), Border=Array([0, 0, 0]),  # noqa: E731
                                        A=Dictionary(S=Name.URI, URI=String(uri)))
    page = pdf.make_indirect(Dictionary(
        Type=Name.Page, MediaBox=Array([0, 0, 612, 792]), Resources=Dictionary(Font=Dictionary(F1=font)),
        Contents=pdf.make_stream(body),
        # Both links are written in the list itself; the first has its corners the other way up, as the converter writes them
        Annots=Array([link([150, 712, 240, 696], "https://example.org/project"),
                      link([290, 696, 360, 712], "https://example.org/second")])))
    pdf.Root.Pages = pdf.make_indirect(Dictionary(Type=Name.Pages, Kids=Array([page]), Count=1))
    page.Parent = pdf.Root.Pages
    pdf.save(path)
    return path


def test_links_written_in_the_pages_list_are_tagged(tmp_path):
    src = _doc(tmp_path / "links.pdf")
    dst = tmp_path / "out.pdf"
    res = tag_document(src, dst, prepare_analysis(src), title="Links", language="en-US")
    assert res.links == 2
    with pikepdf.open(dst) as pdf:
        findings, _ = check_integrity(pdf)
        annots = pdf.pages[0].Annots
        assert all(a.is_indirect and "/StructParent" in a and str(a.get("/Contents", "")).strip() for a in annots)
    assert [f for f in findings if f.severity == "error"] == []
    links = [e for e in build_tagmap(dst)["elements"] if e["role"] == "Link"]
    assert len(links) == 2
