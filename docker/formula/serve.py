"""The formula reading service's loop (#403).

Takes requests from the Redis list "formula:requests" ({"id", "png": base64, "expires": time}), reads the picture
into LaTeX (RapidLaTeXOCR), and puts the answer on "formula:result:<id>": {"ok": true, "latex": "..."} or
{"ok": false, "error": why}. One picture at a time, about a second each on a CPU. On SIGTERM it finishes the
picture it has and stops, so scaling down never loses a reading.

Settings: REDIS_URL (redis://redis:6379/0), FORMULA_RESULT_TTL_S (900), METRICS_PORT (9464: for Prometheus, #397;
0 turns it off).
"""
import base64
import json
import os
import signal
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import redis

QUEUE = "formula:requests"
RESULT = "formula:result:{id}"
RESULT_TTL = int(os.environ.get("FORMULA_RESULT_TTL_S", "900"))
MAX_PNG = 8 * 1024 * 1024  # a formula's picture is a few hundred kilobytes; anything far larger isn't one

stopping = False
stats = {"busy": 0, "read": 0, "not_read": 0}
_model = None


def log(*parts) -> None:
    print(time.strftime("%H:%M:%S"), *parts, flush=True)


class Metrics(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - the stdlib's name
        if self.path != "/metrics":
            self.send_error(404)
            return
        body = ("# HELP formula_reader_busy 1 while a picture is being read\n# TYPE formula_reader_busy gauge\n"
                f"formula_reader_busy {stats['busy']}\n"
                "# HELP formula_reader_requests_total Pictures read, by result\n"
                "# TYPE formula_reader_requests_total counter\n"
                f'formula_reader_requests_total{{result="read"}} {stats["read"]}\n'
                f'formula_reader_requests_total{{result="not_read"}} {stats["not_read"]}\n').encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def serve_metrics(port: int) -> None:
    try:
        server = HTTPServer(("0.0.0.0", port), Metrics)
    except OSError as exc:
        log("metrics not served on port", port, exc)
        return
    threading.Thread(target=server.serve_forever, daemon=True).start()


def model():
    global _model
    if _model is None:
        from rapid_latex_ocr import LaTeXOCR
        _model = LaTeXOCR()
    return _model


def read(png: bytes) -> dict:
    if not png or len(png) > MAX_PNG or not png.startswith(b"\x89PNG"):
        return {"ok": False, "error": "not a PNG picture of a formula"}
    latex, _ = model()(png)
    latex = (latex or "").strip()
    return {"ok": True, "latex": latex} if latex else {"ok": False, "error": "nothing read"}


def answer(client, rid: str, result: dict) -> None:
    key = RESULT.format(id=rid)
    pipe = client.pipeline()
    pipe.lpush(key, json.dumps(result))
    pipe.expire(key, RESULT_TTL)
    pipe.execute()


def handle(client, raw: bytes) -> None:
    try:
        req = json.loads(raw)
        rid = str(req["id"])
    except (ValueError, KeyError, TypeError):
        log("a request that can't be read was dropped")
        return
    if float(req.get("expires") or 0) and time.time() > float(req["expires"]):
        log(rid, "skipped: nobody is waiting for it any more")
        return
    started = time.time()
    stats["busy"] = 1
    try:
        result = read(base64.b64decode(req.get("png") or ""))
    except Exception as exc:  # noqa: BLE001 - always answer
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:200]}
    finally:
        stats["busy"] = 0
    stats["read" if result.get("ok") else "not_read"] += 1
    try:
        answer(client, rid, result)
    except redis.RedisError as exc:
        log(rid, "answer not delivered:", exc)
        return
    log(rid, "read" if result["ok"] else f"not read ({result['error']})", f"in {time.time() - started:.1f}s")


def main() -> int:
    def stop(*_):
        global stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    client = redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://redis:6379/0"))
    if int(os.environ.get("METRICS_PORT", "9464")):
        serve_metrics(int(os.environ.get("METRICS_PORT", "9464")))
    model()  # loaded before the first request: a reading then takes a second, not ten
    log("formula reading service: waiting on", QUEUE)
    while not stopping:
        try:
            got = client.brpop([QUEUE], timeout=5)
        except redis.RedisError as exc:
            log("queue not reachable:", exc)
            time.sleep(5)
            continue
        if got:
            handle(client, got[1])
    log("stopping")
    return 0


if __name__ == "__main__":
    sys.exit(main())
