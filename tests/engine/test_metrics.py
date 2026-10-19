"""#397: the engine's metrics for Prometheus: what's waiting in Redis (the API's /metrics), and each worker's own
(busy or idle, out of how many places). And the music reader's, served without a client library."""
from __future__ import annotations

import importlib.util
import socket
import sys
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import fakeredis
from prometheus_client import CollectorRegistry, generate_latest

from app import metrics


def _value(text: str, name: str, **labels) -> float | None:
    want = name + ("{" + ",".join(f'{k}="{v}"' for k, v in labels.items()) + "}" if labels else "")
    for line in text.splitlines():
        if line.startswith(want + " "):
            return float(line.split()[-1])
    return None


def test_the_api_reports_what_is_waiting_in_each_queue():
    r = fakeredis.FakeRedis()
    r.lpush("celery", "a", "b")
    r.lpush("omr:requests", "page")
    text = metrics.api_metrics(r).decode()
    assert _value(text, "engine_queue_length", queue="engine") == 2
    assert _value(text, "engine_queue_length", queue="adobe") == 0
    assert _value(text, "engine_queue_length", queue="music") == 1
    assert _value(text, "engine_redis_up") == 1


def test_a_redis_that_doesnt_answer_is_said_not_fatal():
    class Down:
        def llen(self, key):
            raise ConnectionError("no redis")
    assert _value(metrics.api_metrics(Down()).decode(), "engine_redis_up") == 0


def test_a_worker_says_how_busy_it_is(monkeypatch):
    from celery.worker import state
    monkeypatch.setattr(state, "active_requests", {"one"})
    reg = CollectorRegistry()
    reg.register(metrics._Worker(SimpleNamespace(pool=SimpleNamespace(num_processes=2))))
    text = generate_latest(reg).decode()
    assert _value(text, "engine_worker_busy") == 1 and _value(text, "engine_worker_places") == 2


def test_the_music_reader_serves_its_own_metrics(monkeypatch):
    path = Path(__file__).resolve().parents[2] / "docker/audiveris/serve.py"
    spec = importlib.util.spec_from_file_location("omr_serve", path)
    serve = importlib.util.module_from_spec(spec)
    sys.modules["omr_serve"] = serve
    spec.loader.exec_module(serve)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    serve.stats.update(busy=1, read=3, not_read=1)
    serve.serve_metrics(port)
    for _ in range(50):
        try:
            text = urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=2).read().decode()
            break
        except OSError:
            time.sleep(0.05)
    assert _value(text, "music_reader_busy") == 1
    assert _value(text, "music_reader_requests_total", result="read") == 3
