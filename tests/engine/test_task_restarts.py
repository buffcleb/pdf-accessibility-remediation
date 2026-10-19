"""A job whose worker was lost is handed out again by the queue: it runs once more, then fails, and a job that already
finished or was stopped isn't run again (2026-10-16: a 300-page document took its worker down and would have run,
and paid for its AI, every hour)."""
from app.storage import JobStore
from app.worker import tasks


def _store(tmp_path):
    store = JobStore("0f0e0d0c-0b0a-4908-8706-050403020100", root=tmp_path)
    store.create()
    return store


def test_a_lost_job_runs_once_more_then_fails(tmp_path):
    store = _store(tmp_path)
    assert tasks._not_again(store, store.job_id) is None            # first start
    assert tasks._not_again(store, store.job_id) is None            # handed out again after a lost worker
    done = tasks._not_again(store, store.job_id)                    # lost again
    assert done == {"job_id": store.job_id, "status": "failed", "error": "worker_lost"}
    assert store.read_error()["error"] == "worker_lost" and "2 tries" in store.read_error()["detail"]


def test_a_finished_or_stopped_job_isnt_run_again(tmp_path):
    store = _store(tmp_path)
    store.write_error({"error": "stopped", "detail": "The job was stopped before it finished."})
    assert tasks._not_again(store, store.job_id) == {"job_id": store.job_id, "status": "skipped"}
    store2 = JobStore("1f0e0d0c-0b0a-4908-8706-050403020100", root=tmp_path)
    store2.create()
    store2.write_result({"files": {}})
    assert tasks._not_again(store2, store2.job_id)["status"] == "skipped"


def test_the_queue_waits_longer_than_a_job_may_run():
    """Redis hands out a task nobody acknowledged after its visibility timeout (an hour by default): shorter than the
    time limit, a long document got a second copy beside the first."""
    from app.config import get_settings
    from app.worker.celery_app import celery_app

    assert celery_app.conf.broker_transport_options["visibility_timeout"] > get_settings().task_time_limit_s


def test_a_worker_process_grown_by_a_long_document_is_replaced():
    from app.config import get_settings
    from app.worker.celery_app import celery_app

    assert celery_app.conf.worker_max_memory_per_child == get_settings().worker_max_memory_mb * 1024


def test_a_running_job_beats_and_a_lost_one_says_so(tmp_path, monkeypatch):
    """#315: a worker killed mid-way left its last progress on show for hours. The job beats while it runs; a stale
    beat on an unfinished job is reported as lost, with when it will be tried again, or that it won't be."""
    import time

    from app.api import routes
    from app.config import get_settings

    store = _store(tmp_path)
    with tasks._heartbeat(store):
        time.sleep(0.05)
    assert store.last_beat() is not None and time.time() - store.last_beat() < 5
    assert routes._lost(store) is None                                     # beating: still at work

    tasks._not_again(store, store.job_id)                                  # first start
    store._write_json("heartbeat.json", {"at": time.time() - 600})         # ...then nothing for ten minutes
    lost = routes._lost(store)
    assert lost["stage"] == "worker_lost" and lost["will_retry"] is True and lost["starts"] == 1
    taken = store.read_attempts()["at"]
    from datetime import datetime
    assert abs(datetime.fromisoformat(lost["retry_after"]).timestamp() - (taken + get_settings().task_time_limit_s + 900)) < 1

    tasks._not_again(store, store.job_id)                                  # its second (last) start, lost too
    store._write_json("heartbeat.json", {"at": time.time() - 600})
    assert routes._lost(store)["will_retry"] is False and routes._lost(store)["retry_after"] is None
    # A job that never started has no beat: waiting, not lost
    assert routes._lost(JobStore("2f0e0d0c-0b0a-4908-8706-050403020100", root=tmp_path)) is None


def test_the_worker_touches_its_liveness_file_from_its_event_loop(tmp_path, monkeypatch):
    """#335: the consumer's timer touches the file every HEARTBEAT_S; stopping the consumer (lost connection,
    shutdown) cancels it, so the file goes stale and the pod's liveness probe restarts the worker."""
    from app.worker import celery_app as ca

    alive = tmp_path / "worker.alive"
    monkeypatch.setattr(ca, "ALIVE_FILE", alive)
    calls = []

    class Tref:
        cancelled = False

        def cancel(self):
            self.cancelled = True

    class Timer:
        def call_repeatedly(self, secs, fun, priority=0):
            calls.append((secs, fun))
            return tref

    class Consumer:
        timer = Timer()

    tref = Tref()
    step = ca.Heartbeat.__new__(ca.Heartbeat)
    step.tref = None
    step.start(Consumer())
    assert alive.exists() and calls == [(ca.HEARTBEAT_S, ca.touch_alive)]
    step.stop(Consumer())
    assert tref.cancelled and step.tref is None
    assert "Heartbeat" in repr(ca.celery_app.steps["consumer"])
