"""The music reading service's loop.

Takes requests from the Redis list "omr:requests" ({"id", "png": base64, "expires": time}), runs Audiveris on the
picture, and puts the answer on "omr:result:<id>": {"ok": true, "musicxml": base64 of the .mxl} or
{"ok": false, "error": why}. A request with "pdf" (and "pages") instead of "png" is a whole score, read as one
piece: {"ok": true, "files": [{"name", "musicxml"}]} (one file, or one per movement). One picture at a time. On SIGTERM it finishes the picture it has and stops, so
scaling down never loses a reading.

Settings: REDIS_URL (redis://redis:6379/0), OMR_READ_TIMEOUT_S (120: one picture), OMR_PAGE_TIMEOUT_S (90: each
page of a whole score), OMR_RESULT_TTL_S (900).
"""
import base64
import glob
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import redis

QUEUE = "omr:requests"
RESULT = "omr:result:{id}"
AUDIVERIS = os.environ.get("AUDIVERIS_BIN", "/opt/audiveris/bin/Audiveris")
READ_TIMEOUT = int(os.environ.get("OMR_READ_TIMEOUT_S", "120"))
PAGE_TIMEOUT = int(os.environ.get("OMR_PAGE_TIMEOUT_S", "90"))  # a whole score: this long for each of its pages
RESULT_TTL = int(os.environ.get("OMR_RESULT_TTL_S", "900"))

stopping = False


def log(*parts) -> None:
    print(time.strftime("%H:%M:%S"), *parts, flush=True)


def read(data: bytes, kind: str = "png", pages: int = 1) -> dict:
    """Audiveris on one picture of music, or (kind "pdf") on a whole score: every page read as one piece."""
    work = tempfile.mkdtemp(prefix="omr-")
    limit = READ_TIMEOUT if kind == "png" else max(READ_TIMEOUT, PAGE_TIMEOUT * max(1, pages))
    try:
        source = os.path.join(work, f"music.{kind}")
        with open(source, "wb") as f:
            f.write(data)
        try:
            run = subprocess.run([AUDIVERIS, "-batch", "-export", "-output", work, source],
                                 capture_output=True, text=True, timeout=limit, cwd=work)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"no reading within {limit} seconds"}
        found = sorted(glob.glob(os.path.join(work, "**", "*.mxl"), recursive=True))
        if found and kind == "png":
            with open(found[0], "rb") as f:
                return {"ok": True, "musicxml": base64.b64encode(f.read()).decode()}
        if found:  # a score with several movements comes out as one file each
            files = []
            for path in found:
                with open(path, "rb") as f:
                    files.append({"name": os.path.basename(path), "musicxml": base64.b64encode(f.read()).decode()})
            return {"ok": True, "files": files}
        out = run.stdout + run.stderr
        if "No system found" in out:
            return {"ok": False, "error": "no staves were found in the picture"}
        warnings = [line.split("|", 1)[-1].strip() for line in out.splitlines() if line.startswith("WARN")]
        return {"ok": False, "error": (warnings[0] if warnings else f"Audiveris ended with code {run.returncode}")[:200]}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    def stop(*_):
        global stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    client = redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://redis:6379/0"))
    log("music reading service: waiting on", QUEUE)
    while not stopping:
        try:
            got = client.brpop([QUEUE], timeout=5)
        except redis.RedisError as exc:
            log("queue not reachable:", exc)
            time.sleep(5)
            continue
        if not got:
            continue
        try:
            req = json.loads(got[1])
            rid = str(req["id"])
        except (ValueError, KeyError, TypeError):
            log("a request that can't be read was dropped")
            continue
        if float(req.get("expires") or 0) and time.time() > float(req["expires"]):
            log(rid, "skipped: nobody is waiting for it any more")
            continue
        started = time.time()
        try:
            if req.get("pdf"):
                answer = read(base64.b64decode(req["pdf"]), "pdf", int(req.get("pages") or 1))
            else:
                answer = read(base64.b64decode(req["png"]))
        except Exception as exc:  # noqa: BLE001 - always answer
            answer = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]}
        key = RESULT.format(id=rid)
        try:
            pipe = client.pipeline()
            pipe.lpush(key, json.dumps(answer))
            pipe.expire(key, RESULT_TTL)
            pipe.execute()
        except redis.RedisError as exc:
            log(rid, "answer not delivered:", exc)
            continue
        log(rid, "read" if answer["ok"] else f"not read ({answer['error']})", f"in {time.time() - started:.0f}s")
    log("stopping")
    return 0


if __name__ == "__main__":
    sys.exit(main())
