"""Asking a reading service for readings over a Redis queue: the formula reader (formula_ocr) and the music reader
(omr) work the same way. A picture of each is put on the service's list, and the answer comes back on a list of
that request's own. A service that doesn't answer in time costs only the wait.
"""
from __future__ import annotations

import base64
import json
import logging
import time
import uuid
from typing import Callable

from app.config import get_settings

log = logging.getLogger(__name__)


def connect(timeout_s: float):
    """A Redis client whose socket waits longer than the longest wait for an answer, or the wait itself is cut
    short."""
    import redis

    return redis.Redis.from_url(get_settings().redis_url, socket_timeout=timeout_s + 15)


def read_all(pictures: list[bytes], *, queue: str, result: str, timeout_s: float, what: str,
             take: Callable[[dict], dict | None], client=None) -> list[dict]:
    """Ask for a reading of each picture (PNG). One answer per picture, in order: take(answer) when it gives one
    ({"ok": True, ...}), else {"ok": False, "error": why}. Waits timeout_s for the first answer (the service may have
    to start) and for each one after it; once one wait runs out the rest aren't waited for. what: the log's word
    for it ("formulas", "music"). result: the answer list's name, with {id}."""
    if not pictures:
        return []
    try:
        client = client or connect(timeout_s)
        ids = [uuid.uuid4().hex for _ in pictures]
        expires = time.time() + timeout_s * (len(pictures) + 1)
        payloads = [json.dumps({"id": rid, "png": base64.b64encode(png).decode(), "expires": expires})
                    for rid, png in zip(ids, pictures)]
        pipe = client.pipeline()
        for payload in payloads:
            pipe.lpush(queue, payload)
        pipe.execute()
    except Exception as exc:  # noqa: BLE001 - no queue, no reading
        log.warning("%s: the reading service's queue isn't reachable: %s", what, exc)
        return [{"ok": False, "error": "the queue isn't reachable"} for _ in pictures]
    out: list[dict] = []
    gave_up = False
    for rid in ids:
        answer = None
        if not gave_up:
            try:
                got = client.blpop([result.format(id=rid)], timeout=timeout_s)
                answer = json.loads(got[1]) if got else None
            except Exception as exc:  # noqa: BLE001
                log.warning("%s: waiting for a reading failed: %s", what, exc)
        if answer is None:
            gave_up = True
            out.append({"ok": False, "error": "no answer from the reading service"})
        elif (taken := take(answer)) is not None:
            out.append(taken)
        else:
            out.append({"ok": False, "error": str(answer.get("error") or "not read")[:200]})
    if gave_up:
        # Don't leave work behind for a service that isn't there (it also skips requests past their time)
        try:
            for payload in payloads:
                client.lrem(queue, 0, payload)
        except Exception:  # noqa: BLE001
            pass
    return out
