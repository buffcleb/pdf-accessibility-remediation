"""#483: dates a checker can read. PAC failed, then crashed, on a document modification date the source file stored
as UTF-16 (no byte-order mark, cut short); veraPDF doesn't check dates. ModDate becomes the time of remediation;
other dates are written back as plain text when they can be read, and removed when they can't."""
from __future__ import annotations

import datetime as dt

import pikepdf
from pikepdf import Array, Dictionary, Name, String

from app.tagging.metadata import _date_text, apply_document_metadata, fix_dates

NOW = dt.datetime(2026, 10, 21, 14, 5, 9, tzinfo=dt.timezone.utc)
UTF16LE_CUT = "D:2020051303".encode("utf-16-le")[:-1]  # as the file had it: no byte-order mark, the last byte gone


def _pdf():
    pdf = pikepdf.new()
    pdf.add_blank_page()
    return pdf


def test_dates_are_read_the_ways_producers_write_them():
    assert _date_text(String(b"D:20200513035628+03'00")) == "D:20200513035628+03'00"
    assert _date_text(String(UTF16LE_CUT)) == "D:2020051303"
    assert _date_text(String(b"\xfe\xff" + "D:20200513".encode("utf-16-be"))) == "D:20200513"
    assert _date_text(String(b"\xff\xfe" + "D:2020".encode("utf-16-le"))) == "D:2020"
    assert _date_text(String(b" 20200513035628Z ")) == "D:20200513035628Z"  # padding, no "D:"
    assert _date_text(String(b"D:20201513")) is None  # month 15
    assert _date_text(String(b"Tuesday")) is None
    assert _date_text(String(b"")) is None


def test_the_modification_date_is_now_and_the_others_are_mended_or_removed():
    pdf = _pdf()
    pdf.docinfo[Name.ModDate] = String(UTF16LE_CUT)
    pdf.docinfo[Name.CreationDate] = String(b"\xfe\xff" + "D:20200513035628Z".encode("utf-16-be"))
    annot = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Text, Rect=Array([0, 0, 10, 10]),
                                         M=String(b"yesterday")))
    pdf.pages[0].obj.Annots = Array([annot])
    attached = pdf.make_stream(b"x", Type=Name.EmbeddedFile,
                               Params=Dictionary(ModDate=String("D:2019".encode("utf-16-le"))))
    pdf.Root.Names = Dictionary(EmbeddedFiles=Dictionary(Names=Array([String("a.txt"), pdf.make_indirect(
        Dictionary(Type=Name.Filespec, F=String("a.txt"), UF=String("a.txt"), EF=Dictionary(F=attached)))])))
    assert fix_dates(pdf, NOW) == 4  # the unreadable ModDate, the UTF-16 creation date, the annotation's, the file's
    assert bytes(pdf.docinfo.ModDate) == b"D:20261021140509Z"
    assert bytes(pdf.docinfo.CreationDate) == b"D:20200513035628Z"
    assert "/M" not in pdf.pages[0].obj.Annots[0]
    assert bytes(attached.Params.ModDate) == b"D:2019"
    # Good dates stay as they are; only ModDate moves on
    assert fix_dates(pdf, NOW + dt.timedelta(hours=1)) == 0
    assert bytes(pdf.docinfo.ModDate) == b"D:20261021150509Z"
    assert bytes(pdf.docinfo.CreationDate) == b"D:20200513035628Z"


def test_the_metadata_step_mends_them_and_keeps_the_xmp_modify_date_in_step():
    pdf = _pdf()
    pdf.docinfo[Name.ModDate] = String(UTF16LE_CUT)
    with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as meta:
        meta["xmp:ModifyDate"] = "2020-05-13T03:56:28+03:00"
    changes = apply_document_metadata(pdf, title="A book", language="en-US", pdfua_part=1)
    assert any("date(s) that weren't valid PDF dates" in c for c in changes)
    assert bytes(pdf.docinfo.ModDate).startswith(b"D:20")
    with pdf.open_metadata() as meta:
        assert not meta["xmp:ModifyDate"].startswith("2020")
