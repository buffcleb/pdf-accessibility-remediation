"""Embedded files need both name entries (PDF/UA 7.11). Found on an article made with Acrobat Distiller, which
attaches its settings as "folder.joboptions" with /F alone: the remediated file failed the check for it."""
import pikepdf
from pikepdf import Array, Dictionary, Name, String

from app.tagging.metadata import apply_document_metadata, name_embedded_files


def _with_attachments(specs):
    pdf = pikepdf.new()
    pdf.add_blank_page()
    made = []
    for spec in specs:
        fs = pdf.make_indirect(Dictionary(Type=Name.Filespec, EF=Dictionary(F=pdf.make_stream(b"settings")), **spec))
        made.append(fs)
    pdf.Root.Names = Dictionary(EmbeddedFiles=Dictionary(Names=Array(
        [x for n, fs in enumerate(made) for x in (String(f"file{n}"), fs)])))
    return pdf, made


def test_the_missing_name_is_copied_from_the_one_that_is_there():
    pdf, (only_f, only_uf, both, neither) = _with_attachments([
        dict(F=String("folder.joboptions")), dict(UF=String("données.csv")),
        dict(F=String("a.txt"), UF=String("a.txt")), {}])
    assert name_embedded_files(pdf) == 3
    assert str(only_f.UF) == "folder.joboptions" and str(only_f.F) == "folder.joboptions"
    assert str(only_uf.F) == "données.csv"
    assert str(both.F) == "a.txt"
    assert str(neither.F) == "attachment" and str(neither.UF) == "attachment"
    assert name_embedded_files(pdf) == 0      # nothing left to do


def test_it_is_part_of_the_document_level_fixes():
    pdf, (spec,) = _with_attachments([dict(F=String("folder.joboptions"))])
    changes = apply_document_metadata(pdf, title="T", language="en-US", pdfua_part=1)
    assert "1 embedded file(s) given the name entries they lacked" in changes and "/UF" in spec
