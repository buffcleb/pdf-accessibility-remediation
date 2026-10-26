"""Characters that are pictures, not text: the note heads of a music font and the line pieces of LaTeX's picture
fonts have no Unicode value to find. Inside a figure or decoration they get private-use values, so every font is
fully mapped (PDF/UA 7.21.7) without flattening the page. Found on a mathematics-of-music textbook."""
import pikepdf
from pikepdf import Array, Dictionary, Name

from app.pipeline import music
from app.pipeline.orchestrator import prepare_analysis
from app.tagging.font_unicode import glyph_to_unicode, map_picture_glyphs, parse_tounicode
from app.tagging.tagger import tag_document
from app.verification.tagmap import build_tagmap
from tests.engine.pdfs import one_page, same_render


def _type3(pdf, names):
    """A font whose characters are small drawn shapes with meaningless names, as a music font's are."""
    procs = Dictionary({f"/{n}": pdf.make_stream(b"500 0 0 0 400 400 d1 100 0 300 300 re f") for n in names})
    return pdf.make_indirect(Dictionary(
        Type=Name.Font, Subtype=Name.Type3, FontBBox=Array([0, 0, 1000, 1000]), FontMatrix=Array([0.001, 0, 0, 0.001, 0, 0]),
        CharProcs=procs, Encoding=Dictionary(Type=Name.Encoding, Differences=Array([33] + [Name("/" + n) for n in names])),
        FirstChar=33, LastChar=33 + len(names) - 1, Widths=Array([500] * len(names))))


def _doc(path, body: bytes, fonts):
    one_page(path, body, fonts)
    return path


def _staff(top: float) -> bytes:
    return b" ".join(f"150 {792 - top - n * 5:.1f} m 450 {792 - top - n * 5:.1f} l S".encode() for n in range(5))


def _maps(path):
    with pikepdf.open(path) as pdf:
        return {str(f.get("/BaseFont", f.get("/Subtype")))[1:]: parse_tounicode(f.ToUnicode.read_bytes())
                for f in pdf.objects if isinstance(f, Dictionary) and f.get("/Type") == Name.Font and "/ToUnicode" in f}


def _tag(src, tmp_path):
    analysis = prepare_analysis(src)
    music.apply(src, analysis, [])
    dst = tmp_path / "out.pdf"
    res = tag_document(src, dst, analysis, title="Example", language="en-US")
    return dst, res


def test_note_heads_in_a_music_figure_get_private_use_values(tmp_path):
    body = (b"BT /F1 11 Tf 100 600 Td (Here is a short example of a scale going up.) Tj ET 0.4 w " + _staff(300)
            + b" BT /M 20 Tf 180 478 Td (!\") Tj 60 4 Td (#) Tj ET"
            + b" BT /F1 11 Tf 100 380 Td (The text carries on below the example.) Tj ET")
    src = _doc(tmp_path / "m.pdf", body, {"M": lambda pdf: _type3(pdf, ["AO", "B4", "BE", "CQ"])})
    dst, res = _tag(src, tmp_path)
    assert list(res.picture_glyphs_mapped.values()) == [3]
    (mapped,) = [m for name, m in _maps(dst).items() if name == "Type3"]
    assert mapped == {33: "", 34: "", 35: ""}  # the three shown; the fourth isn't used
    tm = build_tagmap(dst)
    (figure,) = [e for e in tm["elements"] if e["role"] == "Figure"]
    assert [p for p in tm["pieces"] if p["el"] == figure["i"] and p["kind"] == "text"]  # the notes are in the figure
    assert len([e for e in tm["elements"] if e["role"] == "P"]) == 2                    # and only the two sentences are paragraphs
    assert same_render(src, dst)


def test_the_same_characters_in_a_paragraph_are_left_for_a_person(tmp_path):
    """Text that can't be read isn't made to pass by calling it private-use: only pictures are."""
    body = b"BT /F1 11 Tf 100 600 Td (A sentence with a symbol ) Tj /M 11 Tf (!) Tj /F1 11 Tf ( in the middle of it.) Tj ET"
    src = _doc(tmp_path / "t.pdf", body, {"M": lambda pdf: _type3(pdf, ["AO"])})
    dst, res = _tag(src, tmp_path)
    assert res.picture_glyphs_mapped == {}
    assert "Type3" not in _maps(dst)


def test_a_drawing_fonts_characters_are_strokes_not_paragraphs(tmp_path):
    def line10(pdf):
        return pdf.make_indirect(Dictionary(
            Type=Name.Font, Subtype=Name.Type1, BaseFont=Name("/ABCDEF+LINE10"), FirstChar=0, LastChar=1,
            Widths=Array([500, 500]),
            Encoding=Dictionary(Type=Name.Encoding, Differences=Array([0, Name("/a0"), Name("/a1")]))))
    body = (b"BT /F1 11 Tf 100 600 Td (A diagram drawn with picture-mode lines follows.) Tj ET"
            b" BT /L 10 Tf 200 400 Td <0001> Tj 10 10 Td <00> Tj ET")
    src = _doc(tmp_path / "d.pdf", body, {"L": line10})
    dst, res = _tag(src, tmp_path)
    assert res.picture_glyphs_mapped == {"ABCDEF+LINE10": 2}
    tm = build_tagmap(dst)
    assert [e["text"] for e in tm["elements"] if e["role"] == "P"] == ["A diagram drawn with picture-mode lines follows."]


def test_codes_a_font_already_accounts_for_are_left_alone(tmp_path):
    pdf = pikepdf.new()
    named = pdf.make_indirect(Dictionary(
        Type=Name.Font, Subtype=Name.Type1, BaseFont=Name("/CMSY10"),
        Encoding=Dictionary(Differences=Array([13, Name("/circlecopyrt"), Name("/a77")]))))
    standard = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica,
                                            Encoding=Name.WinAnsiEncoding))
    done, stray = map_picture_glyphs(pdf, {named.objgen: {13, 14}, standard.objgen: {65, 66}})
    assert done == {"CMSY10": 1} and stray == set()
    assert parse_tounicode(named.ToUnicode.read_bytes()) == {13: "◯", 14: ""}
    assert "/ToUnicode" not in standard


def test_more_tex_symbol_names_are_known():
    assert [glyph_to_unicode(n) for n in ("circlecopyrt", "mapsto", "square", "planckover2pi1", "varhexagon")] == \
        ["◯", "↦", "□", "ℏ", "⬡"]
    assert glyph_to_unicode("a113") is None and glyph_to_unicode("AO") is None


def test_a_second_look_at_an_unflattened_page_adds_no_figure_without_a_description(tmp_path):
    """The copy with some pages flattened is analyzed again. On a page that wasn't flattened, a drawing the second
    look counts as a picture and the first didn't has no description: it must not become a figure with no alt
    text (found on two graphs of the textbook, which failed PDF/UA 7.3 in the modified copy only)."""
    from app.pipeline.models import ImageRegion
    from app.pipeline.transfer import transfer_enrichment

    src = _doc(tmp_path / "p.pdf", b"BT /F1 11 Tf 100 600 Td (A page with a graph on it.) Tj ET", {})
    first, again = prepare_analysis(src), prepare_analysis(src)
    described = ImageRegion(id="p0i0", page=0, bbox=(100, 100, 300, 200), xref=0, kind="vector", alt="A rising curve.")
    first.pages[0].images = [described]
    again.pages[0].images = [ImageRegion(id="p0i0", page=0, bbox=(101, 100, 300, 201), xref=0, kind="vector"),
                             ImageRegion(id="p0i1", page=0, bbox=(100, 400, 300, 500), xref=0, kind="vector")]
    transfer_enrichment(first, again, set(), set())
    same, extra = again.pages[0].images
    assert same.alt == "A rising curve." and not same.decorative
    assert extra.decorative and extra.alt is None
    # The same on a page that was flattened: a frame that only looks like a picture once the text over it is an
    # invisible layer (two datasheets and a set of chemistry notes failed 7.3 in their modified copy for this)
    flat = prepare_analysis(src)
    flat.pages[0].images = [ImageRegion(id="p0i0", page=0, bbox=(100, 100, 300, 200), xref=0, kind="vector"),
                            ImageRegion(id="p0i1", page=0, bbox=(100, 400, 300, 500), xref=0, kind="vector")]
    transfer_enrichment(first, flat, {0}, set())
    assert [i.decorative for i in flat.pages[0].images] == [False, True]


def test_a_picture_fonts_symbol_in_a_sentence_is_mapped_and_reported(tmp_path):
    """The music font that draws an example's notes also sets a double-sharp sign in a sentence about it. Leaving
    that one character unmapped sent four pages of a 531-page book, and with them the whole document, through a
    second pass."""
    notes = b"".join(f" BT /M 20 Tf {170 + n * 12} 478 Td (!\") Tj ET".encode() for n in range(12))
    body = (b"BT /F1 11 Tf 100 600 Td (The symbol ) Tj /M 11 Tf (#) Tj /F1 11 Tf ( is used in music for a double sharp.) Tj ET 0.4 w "
            + _staff(300) + notes + b" BT /F1 11 Tf 100 380 Td (The text carries on below the example.) Tj ET")
    src = _doc(tmp_path / "s.pdf", body, {"M": lambda pdf: _type3(pdf, ["AO", "B4", "BH"])})
    dst, res = _tag(src, tmp_path)
    assert list(res.picture_glyphs_mapped.values()) == [3] and res.picture_glyphs_in_text == [1]
    (mapped,) = [m for name, m in _maps(dst).items() if name == "Type3"]
    assert mapped == {33: "\ue021", 34: "\ue022", 35: "\ue023"}
