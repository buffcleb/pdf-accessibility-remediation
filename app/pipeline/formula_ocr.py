"""Formulas read from their pictures, asked for over a Redis queue (#403).

The reader (RapidLaTeXOCR, docker/formula) is a separate service, like the music reader: the engine puts a picture
of each formula on a list, the service takes it, and puts its LaTeX (or why it couldn't) on a list of that
request's own. On a cluster the service is scaled from nothing by the length of the request list, so the first
answer of a document can take a minute or two (the pod has to start).

Its reading is independent of the AI's: two different methods rarely make the same mistake, so when they agree
the formula is very likely right, and when they don't, a person is asked (app/pipeline/mathml.py). A request the
service never answers costs nothing but the wait: the formula is checked as before.
"""
from __future__ import annotations

import logging

from app.config import get_settings
from app.pipeline import reading_queue

log = logging.getLogger(__name__)

QUEUE = "formula:requests"
RESULT = "formula:result:{id}"
MARGIN = 3  # points around a formula's box: its strokes reach a little past the box of its characters
DPI = 200


def pictures(pdf_path, formulas: list[dict]) -> list[bytes | None]:
    """A PNG of each formula's place on its page (None where it has none)."""
    import pymupdf as fitz

    out: list[bytes | None] = []
    doc = fitz.open(pdf_path)
    try:
        for f in formulas:
            box, page = f.get("bbox"), f.get("page")
            if not box or page is None or not 0 <= page < doc.page_count:
                out.append(None)
                continue
            clip = fitz.Rect(box[0] - MARGIN, box[1] - MARGIN, box[2] + MARGIN, box[3] + MARGIN)
            clip &= doc[page].rect
            out.append(doc[page].get_pixmap(clip=clip, dpi=DPI).tobytes("png") if not clip.is_empty else None)
    finally:
        doc.close()
    return out


def read_all(images: list[bytes], client=None) -> list[dict]:
    """Ask for a reading of each picture (PNG). One answer per picture, in order: {"ok": True, "latex": str}, or
    {"ok": False, "error": why}. Waits `formula_ocr_timeout_s` for the first answer (the service may have to start)
    and for each one after it; once one wait runs out the rest aren't waited for."""
    return reading_queue.read_all(
        images, queue=QUEUE, result=RESULT, timeout_s=get_settings().formula_ocr_timeout_s, what="formulas",
        take=lambda a: {"ok": True, "latex": str(a["latex"])[:4000]} if a.get("ok") and a.get("latex") else None,
        client=client)
