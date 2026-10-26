"""MathML inside the PDF (#403): each formula's MathML attached to its Formula tag, as LaTeX's tagging does, so a
reader or assistive technology that understands MathML has the formula itself, not only its read-aloud text.

Only in a PDF 2.0 file (PDF/UA-2, #409): associated files are PDF 2.0's. The MathML goes in as an embedded file on
the tag (/AF, /AFRelationship /Supplement, subtype application/mathml+xml), named and described the way
LaTeX's are (its files pass PDF/UA-2). The read-aloud text (Alt or ActualText) stays: the MathML is beside it, not
instead of it. A Formula that already carries MathML (the document's own) is left as it is.

Formulas are matched to their tags by place: the tag on the formula's page whose content covers most of the
formula's box (and that mostly lies inside it), the way the review's findings find the tag a reading is on. That's
the tag carrying the formula's reading: a Formula, a Figure (a formula drawn as a picture), or a text tag (a
paragraph or span) whose read-aloud text the AI wrote (only an equation in pieces, or a scanned one, becomes a
Formula). A text tag must have read-aloud text, so a sentence around a formula never gets its MathML.
"""
from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

import pikepdf
from pikepdf import Array, Dictionary, Name, String

from app.tagging.conformance import conformance
from app.tagging.mathml_tags import formula_mathml

log = logging.getLogger(__name__)

ROLES = ("Formula", "Figure")
TEXT_ROLES = ("P", "Span", "LBody", "LI", "TD", "TH", "Caption", "Lbl")


def _overlap(a, b) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def _filespec(pdf: pikepdf.Pdf, mathml: str, n: int) -> Dictionary:
    stream = pdf.make_stream(mathml.encode("utf-8"))
    stream.Type, stream.Subtype = Name.EmbeddedFile, Name("/application/mathml+xml")
    stream.Params = Dictionary(ModDate=String(dt.datetime.now(dt.timezone.utc).strftime("D:%Y%m%d%H%M%SZ")))
    name = f"mathml-{n}.xml"
    return pdf.make_indirect(Dictionary(Type=Name.Filespec, F=String(name), UF=String(name),
                                        Desc=String(f"MathML of formula {n}"), AFRelationship=Name.Supplement,
                                        EF=Dictionary(F=stream, UF=stream)))


def embed(path: Path, formulas: list[dict]) -> int:
    """Attach each formula's MathML to the tag carrying its reading in the PDF 2.0 file at `path`, in place.
    formulas: {"page" (0-based), "bbox", "mathml"}. Returns how many were attached; a PDF 1.x file gets none (0)."""
    todo = [f for f in formulas if f.get("mathml") and f.get("bbox")]
    if not todo:
        return 0
    from app.verification.integrity import check_integrity
    from app.verification.semantic import SemanticChecker

    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        if not conformance(str(pdf.pdf_version)).mathml:
            return 0
        _, index = check_integrity(pdf)
        if not index.elements:
            return 0
        checker = SemanticChecker(path, pdf, index, max_pages=100_000)
        checker._collect()
        candidates = []
        for e in index.elements:
            if e.role not in ROLES and not (e.role in TEXT_ROLES and (str(e.obj.get("/ActualText") or "").strip()
                                                                      or str(e.obj.get("/Alt") or "").strip())):
                continue
            box = checker.subtree(e).bbox
            if box is not None:
                candidates.append((e, checker._page_of(e), box))
        done, used = 0, set()
        for f in todo:
            best, best_ov = None, 0.0
            for e, page, box in candidates:
                if page != f["page"] or e.index in used:
                    continue
                ov = _overlap(f["bbox"], box)
                area = max(1.0, (box[2] - box[0]) * (box[3] - box[1]))
                if ov >= 0.5 * area and ov > best_ov:
                    best, best_ov = e, ov
            if best is None or formula_mathml(best.obj)[0] is not None:
                continue
            used.add(best.index)
            af = best.obj.get("/AF")
            af = Array(list(af)) if isinstance(af, Array) else Array([af]) if isinstance(af, Dictionary) else Array()
            af.append(_filespec(pdf, f["mathml"], done + 1))
            best.obj.AF = af
            done += 1
        if done:
            pdf.save(path)
    log.info("MathML attached to %d formula tag(s) in %s", done, Path(path).name)
    return done
