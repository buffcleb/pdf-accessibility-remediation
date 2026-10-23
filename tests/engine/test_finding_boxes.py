"""#426: review items no tag holds say where they are on the page, so Show can outline the area."""
import pikepdf
from pikepdf import Array, Dictionary, Name

from app.verification.integrity import check_integrity
from app.verification.model import MAX_BOXES, area_boxes
from app.verification.semantic import check_semantics
from app.verification.verify import _ai_read_findings

HIDDEN = ["This paragraph was drawn as an artifact by mistake", "so a screen reader never reads any of",
          "its three lines, which sit in the body of the page"]


def test_glyphs_are_gathered_into_lines_and_lines_into_blocks():
    # Two lines of glyphs, 10 apart: two areas, top first
    glyphs = [(72 + 7 * n, y, 78 + 7 * n, y + 10) for y in (120, 100) for n in range(10)]
    assert area_boxes(glyphs) == [[72.0, 100.0, 141.0, 110.0], [72.0, 120.0, 141.0, 130.0]]
    # Sixty lines are too many to list: they come back as at most MAX_BOXES blocks covering them all
    many = [(72, 100 + 12 * n, 300, 110 + 12 * n) for n in range(60)]
    out = area_boxes(many)
    assert 0 < len(out) <= MAX_BOXES
    assert min(a[1] for a in out) == 100 and max(a[3] for a in out) == 818
    assert area_boxes([]) == [] and area_boxes([(5, 5, 5, 9)]) == []  # nothing, or nothing with an area


def _hidden_body(tmp_path):
    """A tagged heading, and three lines of body text marked as an artifact."""
    pdf = pikepdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica,
                                        Encoding=Name.WinAnsiEncoding))
    lines = "".join(f"BT /F1 11 Tf 72 {500 - 16 * n} Td ({t}) Tj ET\n" for n, t in enumerate(HIDDEN))
    content = f"/H1 <</MCID 0>> BDC BT /F1 20 Tf 72 700 Td (A heading) Tj ET EMC\n/Artifact BMC\n{lines}EMC\n"
    pdf.pages.append(pikepdf.Page(Dictionary(Type=Name.Page, MediaBox=[0, 0, 612, 792],
                                             Resources=Dictionary(Font=Dictionary(F1=font)),
                                             Contents=pdf.make_stream(content.encode()))))
    page = pdf.pages[0].obj
    page.StructParents = 0
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    doc = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Document, P=root))
    h1 = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.H1, P=doc, Pg=page, K=0))
    doc.K = Array([h1])
    root.K = doc
    root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array([0, Array([h1])])))
    pdf.Root.StructTreeRoot = root
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    path = tmp_path / "hidden.pdf"
    pdf.save(path)
    return path


def test_body_text_marked_as_an_artifact_says_where_it_is(tmp_path):
    path = _hidden_body(tmp_path)
    with pikepdf.open(path) as pdf:
        _, index = check_integrity(pdf)
        findings, _ = check_semantics(path, pdf, index)
    (f,) = [f for f in findings if f.issue == "text in the body of the page is marked as an artifact"]
    assert f.element is None and f.page == 1
    # One area per line, top first, in the page's top-down space (the lines' baselines are 292, 308 and 324 down)
    assert [(b[0], round(b[1]), round(b[3])) for b in f.boxes] == [(72, 283, 294), (72, 299, 310), (72, 315, 326)]
    assert all(b[2] > 250 for b in f.boxes)
    assert "boxes" in f.to_dict()


def test_the_ai_reading_of_a_block_no_tag_holds_carries_the_block():
    class NoTags:
        elements: list = []

    (f,) = _ai_read_findings(NoTags(), None, [(2, (100.0, 200.0, 300.0, 240.0), "chemistry", "water")])
    assert (f.page, f.element, f.boxes) == (3, None, [[100.0, 200.0, 300.0, 240.0]])
