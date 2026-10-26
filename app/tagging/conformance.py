"""Which part of PDF/UA a file is tagged, labelled and checked by, decided in one place (#445 part 1).

PDF/UA-1 is for PDF 1.x files and PDF/UA-2 for PDF 2.0 ones (#409): a PDF 2.0 file can only be PDF/UA-2, and a 1.x
file is what the engine is set to (PDFUA_PART, 1). The tagger (the XMP identifier and the Document's namespace), the
Adobe route, the checker (which veraPDF flavour gives the verdict) and MathML attachment all ask this module, rather
than each comparing the PDF version itself.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Conformance:
    part: int  # 1 or 2: the PDF/UA part the file is tagged, labelled and checked by
    pdf_version: str  # the file's own ("1.7", "2.0")

    @property
    def namespaces(self) -> bool:
        """PDF 2.0 namespaces are written (the Document in the PDF 2.0 namespace, as PDF/UA-2 asks: 8.2.5.2)."""
        return self.part == 2

    @property
    def mathml(self) -> bool:
        """MathML may be attached to Formulas as associated files: a PDF 2.0 feature."""
        return self.pdf_version >= "2.0"


def conformance(pdf_version: str, requested: int | None = None) -> Conformance:
    """The conformance of a file of this PDF version: PDF/UA-2 for PDF 2.0, else the part asked for (by default the
    configured one)."""
    if pdf_version >= "2.0":
        return Conformance(2, pdf_version)
    if requested is None:
        from app.config import get_settings
        requested = get_settings().pdfua_part
    return Conformance(requested, pdf_version)
