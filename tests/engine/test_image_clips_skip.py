"""The semantic checker walks each page's content stream for the clips that pictures are drawn under. A page that
can't draw a picture (no image among its resources, no inline image in its stream) needn't be walked: on a long
text document that walk was most of the checker's own time, three times a job."""
import pikepdf
import pymupdf as fitz
from pikepdf import Array, Dictionary, Name

from app.verification import semantic
from app.verification.semantic import _image_clips


def _pdf(path, content: bytes, with_image: bool):
    pdf = pikepdf.new()
    res = Dictionary(Font=Dictionary(F1=pdf.make_indirect(Dictionary(
        Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica, Encoding=Name.WinAnsiEncoding))))
    if with_image:
        res.XObject = Dictionary(Im0=pdf.make_stream(b"\xff" * 12, Type=Name.XObject, Subtype=Name.Image, Width=2,
                                                      Height=2, ColorSpace=Name.DeviceRGB, BitsPerComponent=8))
    page = pdf.make_indirect(Dictionary(Type=Name.Page, MediaBox=Array([0, 0, 200, 200]), Resources=res,
                                        Contents=pdf.make_stream(content)))
    pdf.Root.Pages = pdf.make_indirect(Dictionary(Type=Name.Pages, Kids=Array([page]), Count=1))
    page.Parent = pdf.Root.Pages
    pdf.save(path)


def _walks(monkeypatch) -> list:
    walked = []
    original = pikepdf.parse_content_stream

    def counting(obj, *a, **k):
        walked.append(obj)
        return original(obj, *a, **k)
    monkeypatch.setattr(semantic.pikepdf, "parse_content_stream", counting)
    return walked


def test_a_page_of_text_alone_is_not_walked(tmp_path, monkeypatch):
    _pdf(tmp_path / "t.pdf", b"BT /F1 12 Tf 20 150 Td (Only words) Tj ET", with_image=False)
    walked = _walks(monkeypatch)
    with pikepdf.open(tmp_path / "t.pdf") as pdf:
        assert _image_clips(pdf.pages[0]) == {}
    assert walked == []


def test_a_page_with_a_clipped_picture_is_still_walked(tmp_path, monkeypatch):
    _pdf(tmp_path / "i.pdf", b"q 10 10 50 50 re W n 100 0 0 100 0 0 cm /Im0 Do Q", with_image=True)
    walked = _walks(monkeypatch)
    with pikepdf.open(tmp_path / "i.pdf") as pdf:
        clips = _image_clips(pdf.pages[0])
    assert len(walked) == 1 and clips[None] == [fitz.Rect(10, 10, 60, 60)]


def test_a_page_with_an_inline_image_is_still_walked(tmp_path, monkeypatch):
    _pdf(tmp_path / "b.pdf", b"q 5 5 20 20 re W n 40 0 0 40 0 0 cm BI /W 1 /H 1 /CS /G /BPC 8 ID \x80 EI Q",
         with_image=False)
    walked = _walks(monkeypatch)
    with pikepdf.open(tmp_path / "b.pdf") as pdf:
        clips = _image_clips(pdf.pages[0])
    assert len(walked) == 1 and clips[None] == [fitz.Rect(5, 5, 25, 25)]
