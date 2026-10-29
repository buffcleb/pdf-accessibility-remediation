"""Composite fonts whose own ToUnicode map is wrong (app/tagging/wrong_maps.py). Foxit PhantomPDF Printer writes each
glyph's number in the original font instead of its character, and U+0000 for glyphs it can't place; a 72-page audit
report read "/,%(57" for LIBERTY on 41 pages. The files here are built the same way from Liberation Sans, whose glyphs
are in the Macintosh standard order like Arial's."""
from pathlib import Path

import pikepdf
import pymupdf as fitz
import pytest
from fontTools.ttLib import TTFont
from pikepdf import Array, Dictionary, Name

from app.tagging.font_unicode import _entries, add_missing_tounicode
from tests.engine.pdfs import same_render

LIBERATION = Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf")
needs_font = pytest.mark.skipif(not LIBERATION.is_file(), reason="Liberation Sans isn't installed")

TEXT = ["The County Board of Education was established under the laws of the State",
        "and operates under the guidance of a board elected by the voters every year",
        "Cash and cash equivalents consist of cash on hand and demand deposits today"]


def _cmap(entries: dict[int, str]) -> bytes:
    body = "\n".join(f"<{cid:04X}> <{t.encode('utf-16-be').hex().upper()}>" for cid, t in sorted(entries.items()))
    return ("/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n"
            "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
            "/CMapName /Adobe-Identity-UCS def /CMapType 2 def\n1 begincodespacerange <0000> <FFFF> endcodespacerange\n"
            f"{len(entries)} beginbfchar\n{body}\nendbfchar\nendcmap CMapName currentdict /CMap defineresource pop "
            "end end").encode()


def _printed(path, lines=TEXT, mapping=None):
    """A page of `lines` in Liberation Sans as a composite font (CID = glyph number), with a map from
    mapping(glyph number, character) (by default the character itself)."""
    tt = TTFont(str(LIBERATION))
    cmap, scale = tt.getBestCmap(), 1000 / tt["head"].unitsPerEm
    used: dict[int, str] = {}
    ops = [b"BT /F0 11 Tf"]
    for i, line in enumerate(lines):
        gids = [tt.getGlyphID(cmap[ord(ch)]) for ch in line]
        used.update(zip(gids, line))
        ops.append(f"1 0 0 1 60 {720 - 20 * i} Tm <{''.join(f'{g:04X}' for g in gids)}> Tj".encode())
    ops.append(b"ET")
    mapping = mapping or (lambda gid, ch: ch)
    pdf = pikepdf.new()
    program = pdf.make_stream(LIBERATION.read_bytes())
    widths = Array([x for g in sorted(used) for x in (g, Array([round(tt["hmtx"][tt.getGlyphOrder()[g]][0] * scale)]))])
    descriptor = Dictionary(Type=Name.FontDescriptor, FontName=Name("/ABCDEF+FEF19A0056A"), Flags=32,
                            FontBBox=Array([-200, -300, 1200, 1000]), ItalicAngle=0, Ascent=900, Descent=-200,
                            CapHeight=700, StemV=80, FontFile2=program)
    cid = Dictionary(Type=Name.Font, Subtype=Name.CIDFontType2, BaseFont=Name("/ABCDEF+FEF19A0056A"),
                     CIDSystemInfo=Dictionary(Registry=pikepdf.String("Adobe"), Ordering=pikepdf.String("Identity"),
                                              Supplement=0),
                     FontDescriptor=pdf.make_indirect(descriptor), W=widths, CIDToGIDMap=Name.Identity)
    font = pdf.make_indirect(Dictionary(
        Type=Name.Font, Subtype=Name.Type0, BaseFont=Name("/ABCDEF+FEF19A0056A"), Encoding=Name("/Identity-H"),
        DescendantFonts=Array([pdf.make_indirect(cid)]),
        ToUnicode=pdf.make_stream(_cmap({g: mapping(g, ch) for g, ch in used.items()}))))
    page = Dictionary(Type=Name.Page, MediaBox=Array([0, 0, 612, 792]), Resources=Dictionary(Font=Dictionary(F0=font)),
                      Contents=pdf.make_stream(b"\n".join(ops)))
    pdf.pages.append(pikepdf.Page(page))
    pdf.save(path)
    return used


def _text(path) -> str:
    with fitz.open(path) as doc:
        return " ".join(doc[0].get_text().split())


def _map(path) -> dict[int, str]:
    with pikepdf.open(path) as pdf:
        font = next(o for o in pdf.objects if isinstance(o, Dictionary) and o.get("/Subtype") == Name.Type0)
        return {int(src, 16): t for src, t in _entries(font.ToUnicode.read_bytes().decode("latin-1"))}


@pytest.fixture
def no_ocr(monkeypatch):
    monkeypatch.setattr("app.pipeline.ocr.tesseract_available", lambda: False)


@needs_font
def test_an_empty_glyph_mapped_to_nothing_becomes_a_space(tmp_path, no_ocr):
    src, dst = tmp_path / "in.pdf", tmp_path / "out.pdf"
    _printed(src, mapping=lambda gid, ch: "\x00" if ch == " " else ch)
    fixed = add_missing_tounicode(src, dst)
    assert fixed == ["ABCDEF+FEF19A0056A (wrong map: 1 empty glyph(s) as spaces)"]
    assert _map(dst)[3] == " " and _text(dst) == " ".join(TEXT)
    assert same_render(src, dst)


@needs_font
def test_a_map_of_glyph_numbers_is_read_in_the_standard_order(tmp_path, no_ocr):
    src, dst = tmp_path / "in.pdf", tmp_path / "out.pdf"
    _printed(src, mapping=lambda gid, ch: chr(gid))  # what Foxit writes: "/,%(57" for LIBERTY
    assert "7KH" in _text(src)
    (fixed,) = add_missing_tounicode(src, dst)
    assert "read as glyph numbers" in fixed
    assert _text(dst) == " ".join(TEXT)
    assert same_render(src, dst)


@needs_font
def test_a_right_map_is_left_alone(tmp_path, no_ocr):
    src, dst = tmp_path / "in.pdf", tmp_path / "out.pdf"
    _printed(src, lines=TEXT + ["(1,234.56) $ % / ; : - * [ ] # @ ! ? & + = < >"])
    assert add_missing_tounicode(src, dst) == []


@needs_font
def test_a_few_words_are_not_enough_to_read_a_map_as_glyph_numbers(tmp_path, no_ocr):
    src, dst = tmp_path / "in.pdf", tmp_path / "out.pdf"
    _printed(src, lines=["Total Net Position"], mapping=lambda gid, ch: chr(gid))
    assert add_missing_tounicode(src, dst) == []


@needs_font
def test_ocr_reads_the_glyphs_the_map_leaves_out(tmp_path):
    from app.pipeline.ocr import tesseract_available

    if not tesseract_available():
        pytest.skip("Tesseract isn't installed")
    src, dst = tmp_path / "in.pdf", tmp_path / "out.pdf"
    _printed(src, mapping=lambda gid, ch: "\x00" if ch in " Cyw" else chr(gid))
    (fixed,) = add_missing_tounicode(src, dst)
    assert "read as glyph numbers" in fixed and "character(s) read by OCR" in fixed and "still" not in fixed
    assert _text(dst) == " ".join(TEXT)
    assert same_render(src, dst)


def test_veraPDF_names_the_font_of_a_glyph_without_unicode():
    """veraPDF lists 200 checks at most: the pages it names for a font with a wrong map can be a few of those that use
    the font, so the font's name is what the flattening goes by (orchestrator._destructive_candidates)."""
    from app.validation.verapdf import parse_report

    def report(clause, test="2"):
        return f"""<?xml version="1.0"?><report><jobs><job>
<validationReport profileName="PDF/UA-1 validation profile" isCompliant="false">
<details passedRules="105" failedRules="1" passedChecks="10" failedChecks="1">
<rule specification="ISO 14289-1:2014" clause="{clause}" testNumber="{test}" status="failed" failedChecks="1">
<description>x</description>
<check status="failed"><context>root/document[0]/pages[7](214 0 obj PDPage)/contentStream[0](318 0 obj PDSemanticContentStream)/operators[43]/usedGlyphs[0](FPRVML+FEFEF3A587B FPRVML+FEFEF3A587B 1 0 192194474 7 true)</context></check>
</rule></details></validationReport></job></jobs></report>""".encode()

    assert parse_report(report("7.21.7"), "ua1").failing_fonts(("7.21",)) == {"FEFEF3A587B"}
    # A code missing from the map, or a width that doesn't match, is one glyph: not every page with the font fails
    assert parse_report(report("7.21.7", "1"), "ua1").failing_fonts(("7.21",)) == set()
    assert parse_report(report("7.21.5"), "ua1").failing_fonts(("7.21",)) == set()


@needs_font
def test_flattening_doesnt_copy_text_from_a_font_with_a_wrong_map(tmp_path, monkeypatch):
    from app.tagging import destructive

    monkeypatch.setattr("app.pipeline.ocr.tesseract_available", lambda: False)
    src = tmp_path / "in.pdf"
    _printed(src, mapping=lambda gid, ch: chr(gid))
    with fitz.open(src) as doc:
        font, _ = destructive.text_layer_font()
        assert any("7KH" in s[1] for s in destructive._text_spans(doc[0], font))
        assert destructive._text_spans(doc[0], font, frozenset({"FEF19A0056A"})) == []

