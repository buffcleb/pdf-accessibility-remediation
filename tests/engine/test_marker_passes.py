"""With kept tags, the content marker used to run twice over every page: once on a copy, only to find artifact
sections holding body text, then for real. It now runs once, and again only on the pages that have such a section;
the result is the same either way."""
import pikepdf
from pikepdf import Array, Dictionary, Name

from app.pipeline.orchestrator import prepare_analysis
from app.tagging import tagger
from app.tagging.tagger import tag_document
from tests.engine.test_kept_tags_order import _slide_pieces, _tagged, _text


def _runs(monkeypatch) -> list:
    runs = []
    original = tagger.ContentMarker.run

    def counting(self):
        runs.append(self.page.objgen)
        return original(self)
    monkeypatch.setattr(tagger.ContentMarker, "run", counting)
    return runs


def _tagged_plain(path):
    """Two kept-tags pages whose artifacts are page furniture only: nothing to release."""
    pdf = pikepdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica,
                                        Encoding=Name.WinAnsiEncoding))
    pages, elems = [], []
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    doc = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Document, P=root))
    for n in range(2):
        page = pdf.make_indirect(Dictionary(
            Type=Name.Page, MediaBox=Array([0, 0, 612, 792]), Resources=Dictionary(Font=Dictionary(F1=font)),
            Contents=pdf.make_stream(
                b"/Artifact <</Type /Pagination /Subtype /Footer>> BDC BT /F1 9 Tf 72 30 Td (Page %d) Tj ET EMC\n"
                b"/P <</MCID 0>> BDC BT /F1 12 Tf 72 700 Td (Body text on page %d) Tj ET EMC" % (n + 1, n + 1))))
        pages.append(page)
        elems.append(pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.P, P=doc, Pg=page, K=0)))
    pdf.Root.Pages = pdf.make_indirect(Dictionary(Type=Name.Pages, Kids=Array(pages), Count=2))
    nums = []
    for n, page in enumerate(pages):
        page.Parent = pdf.Root.Pages
        page.StructParents = n
        nums += [n, Array([elems[n]])]
    doc.K = Array(elems)
    root.K = Array([doc])
    root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array(nums)))
    pdf.Root.StructTreeRoot = root
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    pdf.save(path)


def test_pages_with_nothing_to_release_are_marked_once(tmp_path, monkeypatch):
    src, out = tmp_path / "in.pdf", tmp_path / "out.pdf"
    _tagged_plain(src)
    runs = _runs(monkeypatch)
    result = tag_document(src, out, prepare_analysis(src), title="T", language="en-US", pdfua_part=1)
    assert result.existing_tags["present"] and not result.existing_tags["rebuilt_tree"]
    assert result.artifacts_released == 0
    assert len(runs) == 2, f"the marker ran {len(runs)} times for two pages"


def test_a_page_whose_artifact_holds_body_text_is_marked_again_and_released(tmp_path, monkeypatch):
    # The slide of test_body_text_hidden_as_an_artifact_is_tagged: one text box marked as a Layout artifact
    pieces = _slide_pieces(540) + [
        (_text(60, 300, "Release notes for every product are published each quarter on the calendar."),
         ("artifact", "<</Type /Layout>>")),
        (_text(60, 20, "Draft for comment only", 9), ("artifact", "<</Type /Pagination>>"))]
    src = _tagged(tmp_path, "hidden", (960, 540), pieces, order=[0, 1, 2, 3])
    out = tmp_path / "out.pdf"
    runs = _runs(monkeypatch)
    result = tag_document(src, out, prepare_analysis(src), title="T", language="en-US", pdfua_part=1)
    assert result.artifacts_released == 1
    assert len(runs) == 2, f"the marker ran {len(runs)} times for one page with a section to release"
