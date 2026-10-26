"""#445 part 1: the PDF/UA part a file is tagged, labelled and checked by, decided in one place."""
from app.tagging.conformance import Conformance, conformance


def test_a_pdf_2_file_is_pdfua_2_whatever_is_asked():
    assert conformance("2.0") == conformance("2.0", 1) == Conformance(2, "2.0")
    assert conformance("2.0").namespaces and conformance("2.0").mathml


def test_a_pdf_1_file_is_the_part_asked_for_or_configured():
    assert conformance("1.7") == Conformance(1, "1.7")  # PDFUA_PART is 1
    assert conformance("1.4", 1).part == 1
    assert conformance("1.7", 2) == Conformance(2, "1.7")  # #445 will ask for this; nothing does yet
    one = conformance("1.7")
    assert not one.namespaces and not one.mathml
    assert conformance("1.7", 2).namespaces and not conformance("1.7", 2).mathml  # MathML files stay PDF 2.0's
