"""Acrobat's invisible text-recognition layer is written in a font holding one empty glyph, so every character of
it refers to the missing glyph (PDF/UA 7.21.8): 202 times in a 332-page guideline. The font gets a second empty
glyph, and nothing on the page changes."""
import io

import pikepdf
from pikepdf import Array, Dictionary, Name

from app.tagging.blank_fonts import _only_an_empty_missing_glyph, fill_blank_fonts
from tests.engine.pdfs import same_render


def _cff(glyphs: dict) -> bytes:
    """A bare CFF font program with the given glyphs (name -> drawing operators)."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.t2CharStringPen import T2CharStringPen

    fb = FontBuilder(unitsPerEm=1000, isTTF=False)
    fb.setupGlyphOrder(list(glyphs))
    fb.setupCharacterMap({})
    strings = {}
    for name, box in glyphs.items():
        pen = T2CharStringPen(1000, None)
        if box:
            pen.moveTo((100, 0)); pen.lineTo((100, 700)); pen.lineTo((600, 700)); pen.lineTo((600, 0)); pen.closePath()  # noqa: E702
        strings[name] = pen.getCharString()
    fb.setupCFF("Hidden", {}, strings, {})
    fb.setupHorizontalMetrics({n: (1000, 0) for n in glyphs})
    fb.setupHorizontalHeader(ascent=1000, descent=-250)
    fb.setupNameTable({"familyName": "Hidden", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    out = io.BytesIO()
    fb.font["CFF "].cff.compile(out, fb.font)
    return out.getvalue()


def _hidden_layer(path, program: bytes, widths=None):
    """A page with a visible line and, over it, the same words as invisible text in a composite font."""
    pdf = pikepdf.new()
    seen = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica,
                                        Encoding=Name.WinAnsiEncoding))
    stream = pdf.make_stream(program)
    stream.Subtype = Name.CIDFontType0C
    desc = pdf.make_indirect(Dictionary(
        Type=Name.FontDescriptor, FontName=Name.Hidden, Flags=4, FontBBox=Array([0, -250, 1000, 1000]),
        ItalicAngle=0, Ascent=1000, Descent=-250, CapHeight=1000, StemV=100, FontFile3=stream))
    cid = Dictionary(Type=Name.Font, Subtype=Name.CIDFontType0, BaseFont=Name.Hidden, DW=1000, FontDescriptor=desc,
                     CIDSystemInfo=Dictionary(Registry=pikepdf.String("Adobe"), Ordering=pikepdf.String("Identity"),
                                              Supplement=0))
    if widths:
        cid.W = Array(widths)
    hidden = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type0, BaseFont=Name.Hidden,
                                          Encoding=Name("/Identity-H"), DescendantFonts=Array([cid])))
    content = (b"BT /F1 12 Tf 72 700 Td (Dose) Tj ET\n"
               b"BT 3 Tr /F2 12 Tf 72 700 Td <0044006F00730065> Tj ET")
    page = pdf.make_indirect(Dictionary(
        Type=Name.Page, MediaBox=Array([0, 0, 612, 792]),
        Resources=Dictionary(Font=Dictionary(F1=seen, F2=hidden)), Contents=pdf.make_stream(content)))
    pdf.Root.Pages = pdf.make_indirect(Dictionary(Type=Name.Pages, Kids=Array([page]), Count=1))
    page.Parent = pdf.Root.Pages
    pdf.save(path)
    return path


def test_a_program_with_only_an_empty_missing_glyph_is_recognized():
    assert _only_an_empty_missing_glyph(_cff({".notdef": False}))
    assert not _only_an_empty_missing_glyph(_cff({".notdef": True}))          # it draws a box: a real missing glyph
    assert not _only_an_empty_missing_glyph(_cff({".notdef": False, "a": True}))  # it has glyphs of its own
    assert not _only_an_empty_missing_glyph(b"not a font")


def test_the_font_gets_an_empty_glyph_and_the_page_is_unchanged(tmp_path):
    src, out = _hidden_layer(tmp_path / "in.pdf", _cff({".notdef": False})), tmp_path / "out.pdf"
    with pikepdf.open(src) as pdf:
        assert fill_blank_fonts(pdf) == ["Hidden"]
        cid = pdf.pages[0].Resources.Font.F2.DescendantFonts[0]
        assert cid.Subtype == Name.CIDFontType2 and "/FontFile2" in cid.FontDescriptor
        assert "/FontFile3" not in cid.FontDescriptor
        assert cid.CIDToGIDMap.read_bytes()[:4] == b"\x00\x01\x00\x01"  # no character is the missing glyph (0) now
        assert fill_blank_fonts(pdf) == []  # and a second pass finds nothing to do
        pdf.save(out)
    assert same_render(src, out)


def test_fonts_that_draw_something_or_carry_their_own_widths_are_left_alone(tmp_path):
    for name, program, widths in (("drawn.pdf", _cff({".notdef": False, "a": True}), None),
                                  ("widths.pdf", _cff({".notdef": False}), [68, [500]])):
        with pikepdf.open(_hidden_layer(tmp_path / name, program, widths)) as pdf:
            assert fill_blank_fonts(pdf) == []
            assert pdf.pages[0].Resources.Font.F2.DescendantFonts[0].Subtype == Name.CIDFontType0
