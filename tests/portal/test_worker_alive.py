"""#335: the portal worker touches its liveness file after each pass that completes, and only then: on 16 October
every pass failed on one document (#324) while the worker looked alive."""
from __future__ import annotations

import os
import time

from portal import worker
from tests.portal.test_jobs import fake_engine, setup  # noqa: F401


def _one_pass(monkeypatch, tmp_path):
    alive = tmp_path / "portal-worker.alive"
    monkeypatch.setattr(worker, "ALIVE_FILE", alive)
    monkeypatch.setattr(worker, "_stop", False)
    monkeypatch.setattr(worker.signal, "signal", lambda *a: None)
    monkeypatch.setattr(worker.time, "sleep", lambda s: setattr(worker, "_stop", True))  # one pass, then stop
    worker.run_forever()
    return alive


def test_a_completed_pass_touches_the_liveness_file(setup, monkeypatch, tmp_path):
    alive = _one_pass(monkeypatch, tmp_path)
    assert alive.exists() and time.time() - alive.stat().st_mtime < 60


def test_a_failed_pass_leaves_it_alone(setup, monkeypatch, tmp_path):
    old = tmp_path / "portal-worker.alive"
    old.touch()
    os.utime(old, (time.time() - 900, time.time() - 900))  # last completed pass 15 minutes ago

    def broken(db):
        raise RuntimeError("every pass fails")
    monkeypatch.setattr(worker, "submit_queued", broken)
    alive = _one_pass(monkeypatch, tmp_path)
    assert time.time() - alive.stat().st_mtime > 800  # still stale: the probe restarts the pod
