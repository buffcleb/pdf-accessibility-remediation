"""Metadata in the form XMP had before it was a standard (Acrobat 5 and earlier). A title and a PDF/UA identifier
added to such a packet aren't found by validators: a 1996 paper failed rules 5 and 7.1 after remediation."""
import pikepdf
from pikepdf import Name, String

from app.tagging.metadata import apply_document_metadata

OLD = b"""<?xpacket begin='' id='W5M0MpCehiHzreSzNTczkc9d' bytes='600'?>
<rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#' xmlns:iX='http://ns.adobe.com/iX/1.0/'>
 <rdf:Description about='' xmlns='http://ns.adobe.com/pdf/1.3/' xmlns:pdf='http://ns.adobe.com/pdf/1.3/'>
  <pdf:Producer>Acrobat PDFWriter 2.01 for Windows</pdf:Producer>
  <pdf:Title>Groundwater Issue Paper</pdf:Title>
 </rdf:Description>
 <rdf:Description about='' xmlns='http://purl.org/dc/elements/1.1/' xmlns:dc='http://purl.org/dc/elements/1.1/'>
  <dc:title>Groundwater Issue Paper</dc:title>
  <dc:creator>Carl D. Palmer</dc:creator>
 </rdf:Description>
</rdf:RDF>
<?xpacket end='r'?>"""


def _pdf(metadata: bytes | None):
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.docinfo[Name.Author] = String("Carl D. Palmer")
    if metadata is not None:
        pdf.Root.Metadata = pdf.make_stream(metadata, Type=Name.Metadata, Subtype=Name.XML)
    return pdf


def _packet(pdf) -> str:
    return pdf.Root.Metadata.read_bytes().decode("utf-8")


def test_an_early_form_of_xmp_is_written_afresh():
    pdf = _pdf(OLD)
    changes = apply_document_metadata(pdf, title="Groundwater Issue Paper", language="en-US", pdfua_part=1)
    packet = _packet(pdf)
    assert "<x:xmpmeta" in packet and "rdf:about" in packet and " about=" not in packet.replace("rdf:about", "")
    assert "pdfuaid:part" in packet and "Groundwater Issue Paper" in packet and "rdf:Alt" in packet
    assert "Carl D. Palmer" in packet                                # the author came back from the document information
    assert any("written afresh" in c for c in changes)
    with pdf.open_metadata() as meta:
        assert meta["dc:title"] == "Groundwater Issue Paper" and meta["pdfuaid:part"] == "1"


def test_modern_metadata_is_kept_and_added_to():
    pdf = _pdf(None)
    with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
        meta["dc:description"] = "kept as it was"
    changes = apply_document_metadata(pdf, title="T", language="en-US", pdfua_part=1)
    assert "kept as it was" in _packet(pdf) and not any("written afresh" in c for c in changes)


def test_metadata_that_isnt_xmp_at_all_is_replaced():
    pdf = _pdf(b"not xml")
    apply_document_metadata(pdf, title="T", language="en-US", pdfua_part=1)
    assert "pdfuaid:part" in _packet(pdf)
