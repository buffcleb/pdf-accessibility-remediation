"""#605: Symbol and Wingdings characters that a document's own map (or a reader of its font) gives as private use code
points are said as Unicode: in the engine's text, and in the document's ToUnicode maps."""
import pikepdf
from pikepdf import Dictionary, Name

from app.pipeline.symbol_chars import family, unicode_for
from app.tagging.font_unicode import _entries, symbol_tounicode


def test_symbol_and_wingdings_private_use_characters_are_mapped_by_their_font():
    assert [unicode_for("ABCDEF+SymbolMT", c) for c in ""] == list("αβ•→≥=(")
    assert [unicode_for("Wingdings-Regular", c) for c in ""] == list("→▪✓➢")
    assert unicode_for("Wingdings-Regular", "") == ""            # a picture with no plain equivalent
    assert unicode_for("CMEX10", "") == "⎛" and unicode_for("Arial", "") == "⎭"  # Adobe's pieces, any font
    assert unicode_for("Arial", "") == ""                        # not a symbol font: meaning unknown
    assert unicode_for("SymbolMT", "a") == "a"                               # only private use is mapped
    assert family("SegoeUISymbol") is None and family("Wingdings2") is None and family("/XYZ+Symbol,Bold") == "symbol"


def _font(pdf, base: str, cmap: str, subtype=Name.TrueType):
    return pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=subtype, BaseFont=Name(base),
                                        ToUnicode=pdf.make_stream(cmap.encode("latin-1"))))


CMAP = ("/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n1 begincodespacerange\n<00><FF>\n"
        "endcodespacerange\n{body}endcmap\nCMapName currentdict /CMap defineresource pop end end\n")


def test_a_documents_own_map_is_rewritten_and_every_other_entry_kept():
    pdf = pikepdf.new()
    # Word's map for a Wingdings arrow: the letter typed and the symbol (with a space in the hex), and another entry
    wing = _font(pdf, "/HLSAIT+Wingdings-Regular",
                 CMAP.format(body="2 beginbfchar\n<21><00e0 f0e0>\n<22> <0041>\nendbfchar\n"))
    assert symbol_tounicode(pdf, wing) == 1
    assert sorted(_entries(wing.ToUnicode.read_bytes().decode("latin-1"))) == [("21", "→"), ("22", "A")]
    # Symbol's Greek letters given as a range of private use values
    sym = _font(pdf, "/XYZ+SymbolMT", CMAP.format(body="1 beginbfrange\n<61> <62> <F061>\nendbfrange\n"))
    assert symbol_tounicode(pdf, sym) == 2
    assert sorted(_entries(sym.ToUnicode.read_bytes().decode("latin-1"))) == [("61", "α"), ("62", "β")]
    # A map with nothing to change is left exactly as it was
    plain = _font(pdf, "/Arial", CMAP.format(body="1 beginbfchar\n<41> <0041>\nendbfchar\n"))
    before = plain.ToUnicode.read_bytes()
    assert symbol_tounicode(pdf, plain) == 0 and plain.ToUnicode.read_bytes() == before


def test_the_analyzer_reads_a_symbol_fonts_private_use_characters_as_unicode():
    from app.pipeline.analyzer import _Run
    span = {"size": 11.0, "flags": 0, "font": "SymbolMT", "color": 0}
    char = {"c": "", "bbox": (0, 0, 5, 10), "origin": (0, 9)}
    assert _Run(span, char, 0).text == "β"
    assert _Run({**span, "font": "Arial"}, char, 0).text == ""
