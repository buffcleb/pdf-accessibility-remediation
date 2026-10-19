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

import base64
import json
import logging
import time
import uuid

from app.config import get_settings

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


def _client():
    import redis

    return redis.Redis.from_url(get_settings().redis_url, socket_timeout=get_settings().formula_ocr_timeout_s + 15)


def read_all(images: list[bytes], client=None) -> list[dict]:
    """Ask for a reading of each picture (PNG). One answer per picture, in order: {"ok": True, "latex": str}, or
    {"ok": False, "error": why}. Waits `formula_ocr_timeout_s` for the first answer (the service may have to start)
    and for each one after it; once one wait runs out the rest aren't waited for."""
    settings = get_settings()
    if not images:
        return []
    try:
        client = client or _client()
        ids = [uuid.uuid4().hex for _ in images]
        expires = time.time() + settings.formula_ocr_timeout_s * (len(images) + 1)
        payloads = [json.dumps({"id": rid, "png": base64.b64encode(png).decode(), "expires": expires})
                    for rid, png in zip(ids, images)]
        pipe = client.pipeline()
        for payload in payloads:
            pipe.lpush(QUEUE, payload)
        pipe.execute()
    except Exception as exc:  # noqa: BLE001 - no queue, no reading
        log.warning("formulas: the reading service's queue isn't reachable: %s", exc)
        return [{"ok": False, "error": "the queue isn't reachable"} for _ in images]
    out: list[dict] = []
    gave_up = False
    for rid in ids:
        answer = None
        if not gave_up:
            try:
                got = client.blpop([RESULT.format(id=rid)], timeout=settings.formula_ocr_timeout_s)
                answer = json.loads(got[1]) if got else None
            except Exception as exc:  # noqa: BLE001
                log.warning("formulas: waiting for a reading failed: %s", exc)
        if answer is None:
            gave_up = True
            out.append({"ok": False, "error": "no answer from the reading service"})
        elif answer.get("ok") and answer.get("latex"):
            out.append({"ok": True, "latex": str(answer["latex"])[:4000]})
        else:
            out.append({"ok": False, "error": str(answer.get("error") or "not read")[:200]})
    if gave_up:
        try:  # don't leave work behind for a service that isn't there
            for payload in payloads:
                client.lrem(QUEUE, 0, payload)
        except Exception:  # noqa: BLE001
            pass
    return out
