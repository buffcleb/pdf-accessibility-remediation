"""Unembedded standard fonts get a metric-compatible substitute in the modified file. Each substitute file is embedded
once and shared, not once per font: a 332-page guideline with a font dictionary per page carried 452 copies of the
same few files, 68.7 MB of a 74.7 MB file (#577)."""
import pikepdf
import pymupdf as fitz
from pikepdf import Dictionary, Name

from app.tagging import font_embed


def _substitutes(tmp_path, monkeypatch):
    """Stand-in substitute files (PyMuPDF's built-in fonts) under the Liberation names, one per style."""
    d = tmp_path / "fonts"
    d.mkdir()
    for fam, base in (("Serif", "tiro"), ("Sans", "helv"), ("Mono", "cour")):
        for style, suffix in (("Regular", ""), ("Bold", "bo"), ("Italic", "it"), ("BoldItalic", "bi")):
            (d / f"Liberation{fam}-{style}.ttf").write_bytes(fitz.Font(base[:2] + suffix if suffix else base).buffer)
    monkeypatch.setattr(font_embed, "_font_dirs", lambda: [str(d)])


def _pages_with_own_fonts(path, pages=30):
    """Each page with its own (unshared) font dictionaries for Helvetica, Helvetica-Bold and Times-Roman, as Word
    and Acrobat often write them."""
    pdf = pikepdf.new()
    for _ in range(pages):
        fonts = Dictionary({
            f"/F{i}": pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name("/" + base),
                                                   Encoding=Name.WinAnsiEncoding))
            for i, base in enumerate(("Helvetica", "Helvetica-Bold", "Times-Roman"))})
        page = pdf.add_blank_page(page_size=(612, 792))
        page.Resources = Dictionary(Font=fonts)
        page.Contents = pdf.make_stream(b"BT /F0 12 Tf 72 700 Td (Plain) Tj /F1 12 Tf (Bold) Tj /F2 12 Tf (Serif) Tj ET")
    pdf.save(path)


def _programs(path) -> dict[tuple, int]:
    """Embedded font program (object id) -> number of fonts using it."""
    out: dict[tuple, int] = {}
    with pikepdf.open(path) as pdf:
        for obj in pdf.objects:
            if isinstance(obj, Dictionary) and obj.get("/Type") == Name.Font:
                ff = obj.FontDescriptor.get("/FontFile2")
                out[ff.objgen] = out.get(ff.objgen, 0) + 1
    return out


def test_each_substitute_embedded_once(tmp_path, monkeypatch):
    _substitutes(tmp_path, monkeypatch)
    src, dst = tmp_path / "in.pdf", tmp_path / "out.pdf"
    _pages_with_own_fonts(src)
    done = font_embed.embed_standard_fonts(src, dst)
    assert len(done) == 90
    programs = _programs(dst)
    assert sorted(programs.values()) == [30, 30, 30]  # three substitutes, each shared by its 30 fonts
    files = sum(len(fitz.Font(n).buffer) for n in ("helv", "hebo", "tiro"))
    assert dst.stat().st_size < 2 * files  # the three files once each, not thirty copies of each


def test_shared_program_survives_a_resave(tmp_path, monkeypatch):
    """The later passes (tagging, ToUnicode and width fixes) open and save the file with pikepdf: one program each."""
    _substitutes(tmp_path, monkeypatch)
    src, dst, again = tmp_path / "in.pdf", tmp_path / "out.pdf", tmp_path / "again.pdf"
    _pages_with_own_fonts(src, pages=5)
    font_embed.embed_standard_fonts(src, dst)
    with pikepdf.open(dst) as pdf:
        pdf.save(again)
    assert sorted(_programs(again).values()) == [5, 5, 5]


def test_each_font_keeps_its_own_widths(tmp_path, monkeypatch):
    """Sharing the program doesn't share the widths: they follow each font's own encoding."""
    _substitutes(tmp_path, monkeypatch)
    src, dst = tmp_path / "in.pdf", tmp_path / "out.pdf"
    pdf = pikepdf.new()
    page = pdf.add_blank_page()
    plain = Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica, Encoding=Name.WinAnsiEncoding)
    odd = Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica,
                     Encoding=Dictionary(Type=Name.Encoding, BaseEncoding=Name.WinAnsiEncoding,
                                         Differences=pikepdf.Array([65, Name.W])))
    page.Resources = Dictionary(Font=Dictionary(F0=pdf.make_indirect(plain), F1=pdf.make_indirect(odd)))
    pdf.save(src)
    font_embed.embed_standard_fonts(src, dst)
    with pikepdf.open(dst) as out:
        fonts = out.pages[0].Resources.Font
        assert fonts.F0.FontDescriptor.FontFile2.objgen == fonts.F1.FontDescriptor.FontFile2.objgen
        assert fonts.F0.Widths[65] != fonts.F1.Widths[65]  # A, and W in its place
