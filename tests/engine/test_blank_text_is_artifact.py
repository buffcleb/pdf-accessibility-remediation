"""A space drawn near no text (Word writes one after a picture on a line of its own) was tagged as a paragraph of its
own: an empty P, which says nothing and which checkers flag. It is decoration now; a real character there still gets
its own paragraph."""
import pikepdf
from pikepdf import Array, Dictionary, Name

from app.pipeline.orchestrator import prepare_analysis
from app.tagging.tagger import tag_document


def _tag(tmp_path, lone: bytes, space_code: int | None = None) -> tuple[int, bytes]:
    """A page of body text and, far from it, one more piece of text; tagged, its P count and content. space_code: the
    font's ToUnicode map says that code is a space (as in Word's subset fonts)."""
    src, out = tmp_path / "in.pdf", tmp_path / "out.pdf"
    pdf = pikepdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica,
                                        Encoding=Name.WinAnsiEncoding))
    if space_code is not None:
        font.ToUnicode = pdf.make_stream(
            b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /Adobe-Identity-UCS def\n"
            b"1 begincodespacerange <00> <FF> endcodespacerange\n"
            b"1 beginbfrange <22> <7e> <0022> endbfrange\n"
            b"1 beginbfchar <%02x> <0020> endbfchar\nendcmap end end" % space_code)
    page = pdf.make_indirect(Dictionary(
        Type=Name.Page, MediaBox=Array([0, 0, 612, 792]), Resources=Dictionary(Font=Dictionary(F1=font)),
        Contents=pdf.make_stream(b"BT /F1 12 Tf 72 700 Td (A paragraph of body text on the page.) Tj ET\n"
                                 b"BT /F1 12 Tf 480 400 Td (" + lone + b") Tj ET")))
    pdf.Root.Pages = pdf.make_indirect(Dictionary(Type=Name.Pages, Kids=Array([page]), Count=1))
    page.Parent = pdf.Root.Pages
    pdf.save(src)
    tag_document(src, out, prepare_analysis(src), title="T", language="en-US", pdfua_part=1)
    with pikepdf.open(out) as pdf:
        ps = sum(1 for o in pdf.objects if isinstance(o, Dictionary) and o.get("/S") == Name.P)
        return ps, pdf.pages[0].Contents.read_bytes()


def test_a_lone_space_is_decoration(tmp_path):
    ps, content = _tag(tmp_path, b" ")
    assert ps == 1
    assert b"480 400 Td\n/Artifact << /Type /Layout >> BDC\n( ) Tj" in content


def test_a_lone_character_is_still_text(tmp_path):
    ps, content = _tag(tmp_path, b"x")
    assert ps == 2
    assert b"480 400 Td\n/P << /MCID 1 >> BDC\n(x) Tj" in content


def test_a_space_by_the_fonts_own_code_is_decoration(tmp_path):
    ps, content = _tag(tmp_path, b"!", space_code=0x21)
    assert ps == 1
    assert b"480 400 Td\n/Artifact << /Type /Layout >> BDC\n(!) Tj" in content
