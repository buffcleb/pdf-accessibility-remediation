"""Optical music recognition, asked for over a Redis queue.

The reader (Audiveris, docker/audiveris) is a separate service that only needs to run while there is music to
read: the engine puts a picture of each example on a list, the service takes it, and puts MusicXML (or why it
couldn't) on a list of that request's own. On a cluster the service is scaled from nothing by the length of the
request list, so the first answer of a document can take a minute or two (the pod has to start).

A request the service never answers costs nothing but the wait: the example is then described by the AI like any
other picture.
"""
from __future__ import annotations

import base64
import json
import logging
import time
import uuid

from app.config import get_settings

log = logging.getLogger(__name__)

QUEUE = "omr:requests"
RESULT = "omr:result:{id}"


def _client():
    import redis

    # Longer than the longest wait for an answer, or the wait itself is cut short
    return redis.Redis.from_url(get_settings().redis_url, socket_timeout=get_settings().omr_timeout_s + 15)


def read_all(pictures: list[bytes], client=None) -> list[dict]:
    """Ask for a reading of each picture (PNG). One answer per picture, in order: {"ok": True, "musicxml": bytes},
    or {"ok": False, "error": why}. Waits `omr_timeout_s` for the first answer (the service may have to start) and
    for each one after it; once one wait runs out the rest aren't waited for."""
    settings = get_settings()
    if not pictures:
        return []
    try:
        client = client or _client()
        ids = [uuid.uuid4().hex for _ in pictures]
        expires = time.time() + settings.omr_timeout_s * (len(pictures) + 1)
        payloads = [json.dumps({"id": rid, "png": base64.b64encode(png).decode(), "expires": expires})
                    for rid, png in zip(ids, pictures)]
        pipe = client.pipeline()
        for payload in payloads:
            pipe.lpush(QUEUE, payload)
        pipe.execute()
    except Exception as exc:  # noqa: BLE001 - no queue, no reading
        log.warning("music: the reading service's queue isn't reachable: %s", exc)
        return [{"ok": False, "error": "the queue isn't reachable"} for _ in pictures]
    out: list[dict] = []
    gave_up = False
    for rid in ids:
        answer = None
        if not gave_up:
            try:
                got = client.blpop([RESULT.format(id=rid)], timeout=settings.omr_timeout_s)
                answer = json.loads(got[1]) if got else None
            except Exception as exc:  # noqa: BLE001
                log.warning("music: waiting for a reading failed: %s", exc)
        if answer is None:
            gave_up = True
            out.append({"ok": False, "error": "no answer from the reading service"})
        elif answer.get("ok") and answer.get("musicxml"):
            out.append({"ok": True, "musicxml": base64.b64decode(answer["musicxml"])})
        else:
            out.append({"ok": False, "error": str(answer.get("error") or "not read")[:200]})
    if gave_up:
        # Don't leave work behind for a service that isn't there (it also skips requests past their time)
        try:
            for payload in payloads:
                client.lrem(QUEUE, 0, payload)
        except Exception:  # noqa: BLE001
            pass
    return out


def read_score(pdf: bytes, pages: int, client=None) -> list[tuple[str, bytes]]:
    """Ask for a whole score to be read as one piece. Returns its MusicXML as (name, data): one file, or one per
    movement; nothing when the service didn't answer in time, couldn't read it, or is an older one that only
    takes pictures. Waits `omr_timeout_s` plus `omr_page_timeout_s` for every page."""
    settings = get_settings()
    wait = settings.omr_timeout_s + settings.omr_page_timeout_s * max(1, pages)
    rid = uuid.uuid4().hex
    payload = json.dumps({"id": rid, "pdf": base64.b64encode(pdf).decode(), "pages": pages, "expires": time.time() + wait})
    try:
        if client is None:
            import redis

            client = redis.Redis.from_url(settings.redis_url, socket_timeout=wait + 15)
        client.lpush(QUEUE, payload)
        got = client.blpop([RESULT.format(id=rid)], timeout=wait)
    except Exception as exc:  # noqa: BLE001 - no queue, no score
        log.warning("music: the whole score wasn't read: %s", exc)
        return []
    if not got:
        try:
            client.lrem(QUEUE, 0, payload)
        except Exception:  # noqa: BLE001
            pass
        log.warning("music: no answer for the whole score within %d seconds", wait)
        return []
    answer = json.loads(got[1])
    if not answer.get("ok"):
        log.warning("music: the whole score wasn't read: %s", answer.get("error"))
        return []
    out = []
    for f in answer.get("files") or []:
        try:
            out.append((str(f["name"]), base64.b64decode(f["musicxml"])))
        except (KeyError, ValueError, TypeError):
            continue
    return out
