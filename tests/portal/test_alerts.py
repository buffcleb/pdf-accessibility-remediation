"""#334: the service tells super admins about its own problems, once when one starts, again every few hours, and when
it clears; Attention shows them and /api/v1/status answers 503 for an outside uptime check."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from portal import alerts
from portal.models import AlertState, Job, MailOutbox, QueuePause
from tests.portal.test_jobs import API, fake_engine, pdf_bytes, run_worker, setup, topup, upload  # noqa: F401

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)


def _alerts_mailed(db):
    db.expire_all()
    return [(m.to_addr, m.subject) for m in db.scalars(select(MailOutbox).where(MailOutbox.kind == "alert")
                                                       .order_by(MailOutbox.created_at))]


def _queued(s, n=1):
    topup(s, s["a"].id, 100)
    ids = [r["job"]["id"] for r in upload(s["c"]["user"], *[(f"d{k}.pdf", pdf_bytes(1)) for k in range(n)]).json()]
    return [s["db"].get(Job, uuid.UUID(i)) for i in ids]


def test_a_queue_that_isnt_moving_is_alerted_reminded_and_cleared(setup):
    s, db = setup, setup["db"]
    (job,) = _queued(s)
    job.created_at = NOW - timedelta(hours=2)
    db.commit()
    assert alerts.evaluate(db, now=NOW) == ["queue_stuck"]
    db.commit()
    assert _alerts_mailed(db) == [("root@admin.test", "Alert: The document queue isn't moving")]
    row = db.get(AlertState, "queue_stuck")
    assert "1 document waiting" in row.detail
    # Not again within the reminder interval; again after it
    assert alerts.evaluate(db, now=NOW + timedelta(hours=1)) == []
    assert alerts.evaluate(db, now=NOW + timedelta(hours=4, minutes=1)) == ["queue_stuck"]
    db.commit()
    assert _alerts_mailed(db)[-1][1] == "Still: The document queue isn't moving"
    # It moves: cleared, and said so
    job.status, job.submitted_at = "submitted", NOW + timedelta(hours=5)
    db.commit()
    alerts.evaluate(db, now=NOW + timedelta(hours=5, minutes=1))
    db.commit()
    assert _alerts_mailed(db)[-1][1] == "Cleared: The document queue isn't moving"
    assert db.get(AlertState, "queue_stuck") is None


def test_a_busy_queue_that_is_moving_isnt_stuck_nor_one_waiting_on_purpose(setup):
    s, db = setup, setup["db"]
    jobs = _queued(s, 3)
    jobs[0].status, jobs[0].finished_at = "completed", NOW - timedelta(minutes=50)  # finished within the hour
    db.commit()
    assert "queue_stuck" not in alerts.conditions(db, now=NOW)
    assert "queue_stuck" in alerts.conditions(db, now=NOW + timedelta(minutes=20))  # nothing for 70 minutes
    # Waiting on purpose: tomorrow's daily limit, or a paused service
    jobs[1].progress = {"stage": "waiting_daily_cap"}
    db.add(QueuePause(key="service:standard", paused_by="root@admin.test"))
    db.commit()
    assert "queue_stuck" not in alerts.conditions(db, now=NOW + timedelta(minutes=20))
    # The time is a setting
    s["c"]["super"].patch(f"{API}/admin/settings", json={"alert_queue_minutes": 30})
    db.query(QueuePause).delete()
    jobs[1].progress = None
    db.commit()
    assert "queue_stuck" in alerts.conditions(db, now=NOW - timedelta(minutes=15))


def test_a_worker_that_stops_completing_passes_shows_on_status_and_attention(setup):
    s, db = setup, setup["db"]
    sup = s["c"]["super"]
    assert sup.get(f"{API}/status").json() == {"status": "ok", "problems": []}
    alerts.beat(db, now=datetime.now(timezone.utc) - timedelta(minutes=20))
    db.commit()
    r = sup.get(f"{API}/status")
    assert r.status_code == 503 and r.json() == {"status": "problem", "problems": ["worker_stopped"]}
    assert s["c"]["user"].get(f"{API}/status").status_code == 503  # it needs no sign-in (the uptime check has none)
    alerts.evaluate(db)
    db.commit()
    kinds = [i["kind"] for i in sup.get(f"{API}/admin/attention").json()]
    assert "worker_stopped" in kinds
    assert "worker_stopped" not in [i["kind"] for i in s["c"]["admin_a"].get(f"{API}/groups/{s['a'].id}/attention").json()]
    # The worker's own passes keep it fresh
    run_worker(db)
    alerts.beat(db)
    db.commit()
    assert sup.get(f"{API}/status").status_code == 200


def test_an_engine_without_workers_waits_its_grace_before_anyone_is_told(setup):
    s, db = setup, setup["db"]
    alerts.evaluate(db, engine_asked=True, engine_workers=0, now=NOW)
    db.commit()
    assert db.get(AlertState, "no_engine_workers") is not None and _alerts_mailed(db) == []  # within its 10 minutes
    assert alerts.active(db, now=NOW) == []
    alerts.evaluate(db, now=NOW + timedelta(minutes=5))  # not asked: what was known stays
    assert alerts.evaluate(db, engine_asked=True, engine_workers=None, now=NOW + timedelta(minutes=11)) == ["no_engine_workers"]
    db.commit()
    assert _alerts_mailed(db) == [("root@admin.test", "Alert: The engine has no workers")]
    assert "couldn't be reached" in db.get(AlertState, "no_engine_workers").detail
    alerts.evaluate(db, engine_asked=True, engine_workers=4, now=NOW + timedelta(minutes=12))
    db.commit()
    assert _alerts_mailed(db)[-1][1] == "Cleared: The engine has no workers"


def test_a_problem_that_clears_before_anyone_was_told_says_nothing(setup):
    s, db = setup, setup["db"]
    alerts.evaluate(db, engine_asked=True, engine_workers=0, now=NOW)
    alerts.evaluate(db, engine_asked=True, engine_workers=2, now=NOW + timedelta(minutes=3))
    db.commit()
    assert _alerts_mailed(db) == [] and db.get(AlertState, "no_engine_workers") is None


def test_the_alert_settings_are_kept_and_bounded(setup):
    sup = setup["c"]["super"]
    r = sup.patch(f"{API}/admin/settings", json={"alert_queue_minutes": 90, "alert_repeat_hours": 8})
    assert r.status_code == 200 and (r.json()["alert_queue_minutes"], r.json()["alert_repeat_hours"]) == (90, 8)
    assert sup.patch(f"{API}/admin/settings", json={"alert_queue_minutes": 1}).status_code == 422
    assert setup["c"]["admin_a"].patch(f"{API}/admin/settings", json={"alert_queue_minutes": 90}).status_code == 403


def test_a_backup_that_hasnt_finished_is_alerted_only_where_the_cluster_runs_them(setup, monkeypatch):
    """#333: with BACKUPS=cronjob the deploy sets PORTAL_BACKUP_EXPECTED; with external (the hosts' own backups)
    nothing about backups is ever alerted."""
    from portal.backup_tools import record_backup
    from portal.config import get_portal_settings
    from portal.models import WorkerBeat

    s, db = setup, setup["db"]
    assert alerts.evaluate(db, now=NOW) == [] and db.get(WorkerBeat, alerts.BACKUP) is None  # not expected: nothing
    monkeypatch.setattr(get_portal_settings(), "backup_expected", True)
    alerts.evaluate(db, now=NOW)  # expected from now: the clock starts
    db.commit()
    assert db.get(WorkerBeat, alerts.BACKUP).at == NOW
    assert alerts.evaluate(db, now=NOW + timedelta(hours=25)) == []
    assert alerts.evaluate(db, now=NOW + timedelta(hours=27)) == ["backup_stale"]
    db.commit()
    assert _alerts_mailed(db)[-1][1] == "Alert: The nightly backup hasn't finished"
    record_backup()  # the job's last step
    db.expire_all()
    alerts.evaluate(db)
    db.commit()
    assert _alerts_mailed(db)[-1][1] == "Cleared: The nightly backup hasn't finished"


def test_old_backups_are_pruned_keeping_nightly_and_weekly_ones(tmp_path):
    from portal.backup_tools import prune_backups

    names = ["backup-20261016-020000", "backup-20261015-020000", "backup-20261014-020000",  # this week and last
             "backup-20261007-020000", "backup-20261006-020000",  # the week before
             "backup-20260930-020000", "backup-20260901-020000"]
    for n in names + ["backup-20261016-030000.partial", "something-else"]:
        (tmp_path / n).mkdir()
    gone = prune_backups(tmp_path, daily=2, weekly=3, dry_run=True)
    assert all((tmp_path / n).exists() for n in names)  # a dry run changes nothing
    prune_backups(tmp_path, daily=2, weekly=3)
    left = sorted(p.name for p in tmp_path.iterdir())
    # The newest 2, plus the newest of each of the 3 most recent weeks (16 Oct's week, 7 Oct's, 30 Sep's)
    assert left == ["backup-20260930-020000", "backup-20261007-020000", "backup-20261015-020000",
                    "backup-20261016-020000", "something-else"]
    assert set(gone) == {"backup-20261014-020000", "backup-20261006-020000", "backup-20260901-020000",
                         "backup-20261016-030000.partial"}


def test_a_groups_admins_are_told_once_when_its_pool_runs_low(setup):
    """#358: one email when a pool drops below the threshold (Settings), none again until it's topped up above it."""
    from portal import pool_alerts
    from portal.models import GroupService

    s, db = setup, setup["db"]
    topup(s, s["a"].id, 100)
    gs = db.scalar(select(GroupService).where(GroupService.group_id == s["a"].id))
    def mailed():
        db.expire_all()
        return [(m.to_addr, m.subject) for m in db.scalars(select(MailOutbox).where(MailOutbox.kind == "alert"))]
    assert pool_alerts.check(db) == [] and mailed() == []  # 100 left of 100
    gs.used_pages = 85  # 15 left: below 20 pages
    db.commit()
    assert len(pool_alerts.check(db)) == 1
    db.commit()
    assert mailed() == [("admin@a.test", "Campus A: Standard pages running low")]
    assert pool_alerts.check(db) == []  # still low: not again
    db.commit()
    assert len(mailed()) == 1
    # Not a service alert: super admins' alerts and Attention's service items are unaffected
    assert [r.key for r in alerts.active(db)] == [] and alerts.evaluate(db) == []
    # Topped up above: re-armed; drops again: told again
    gs.used_pages = 0
    db.commit()
    pool_alerts.check(db)
    db.commit()
    gs.used_pages = 95
    db.commit()
    pool_alerts.check(db)
    db.commit()
    assert len(mailed()) == 2 and mailed()[-1][1] == "Campus A: Standard pages running low"
    # The threshold is a setting
    s["c"]["super"].patch(f"{API}/admin/settings", json={"low_pool_percent": 0, "low_pool_pages": 3})
    gs.used_pages = 0
    db.commit()
    pool_alerts.check(db)
    gs.used_pages = 90  # 10 left: not low any more by the new rule
    db.commit()
    assert pool_alerts.check(db) == []


def _firing(name, *descriptions):
    return [{"labels": {"alertname": name, "pod": f"p{i}"}, "annotations": {"description": d}, "state": "firing"}
            for i, d in enumerate(descriptions)]


def test_prometheus_alerts_are_told_like_the_portals_own_one_per_rule(setup):
    """#397: what the deployment's Prometheus has firing goes to super admins with the rest, and on Attention."""
    s, db = setup, setup["db"]
    firing = _firing("PodRestarting", "engine-worker-a restarted 4 times in the last hour.",
                     "portal-worker-b restarted 3 times in the last hour.")
    assert alerts.evaluate(db, prom_asked=True, prom_firing=firing, now=NOW) == ["prom:PodRestarting"]
    db.commit()
    assert _alerts_mailed(db) == [("root@admin.test", "Alert: A pod has restarted several times in the last hour")]
    row = db.get(AlertState, "prom:PodRestarting")
    assert "engine-worker-a" in row.detail and "portal-worker-b" in row.detail
    items = s["c"]["super"].get(f"{API}/admin/attention").json()
    item = next(i for i in items if i["kind"] == "prom:PodRestarting")
    assert item["title"] == "A pod has restarted several times in the last hour" and "describe pod" in item["fix"]
    # A rule the portal doesn't know still alerts, under its own name; more than three pods are summed up
    many = _firing("SomethingNew", *[f"pod {k} is odd." for k in range(5)])
    alerts.evaluate(db, prom_asked=True, prom_firing=firing + many, now=NOW + timedelta(minutes=1))
    db.commit()
    assert _alerts_mailed(db)[-1][1] == "Alert: SomethingNew"
    assert db.get(AlertState, "prom:SomethingNew").detail.endswith("And 2 more.")
    # Prometheus away: what it said stands, and after the grace it's alerted itself
    alerts.evaluate(db, prom_asked=True, prom_firing=None, now=NOW + timedelta(minutes=2))
    assert db.get(AlertState, "prom:PodRestarting") is not None
    assert alerts.evaluate(db, prom_asked=True, prom_firing=None, now=NOW + timedelta(minutes=13)) == ["monitoring_down"]
    db.commit()
    assert _alerts_mailed(db)[-1][1] == "Alert: Monitoring (Prometheus) isn't answering"
    # Back with nothing firing: all cleared
    alerts.evaluate(db, prom_asked=True, prom_firing=[], now=NOW + timedelta(minutes=14))
    db.commit()
    assert {m[1] for m in _alerts_mailed(db)[-3:]} == {
        "Cleared: A pod has restarted several times in the last hour", "Cleared: SomethingNew",
        "Cleared: Monitoring (Prometheus) isn't answering"}
    assert not [r for r in db.scalars(select(AlertState)) if alerts.ours(r.key)]


def test_without_prometheus_nothing_is_asked_and_its_alerts_go(setup):
    s, db = setup, setup["db"]
    alerts.evaluate(db, prom_asked=True, prom_firing=_firing("RedisDown", "Redis is away."), now=NOW)
    alerts.evaluate(db, now=NOW + timedelta(minutes=1))  # MONITORING=off since
    db.commit()
    assert db.get(AlertState, "prom:RedisDown") is None and "monitoring_down" not in alerts.conditions(db, now=NOW)


def test_every_prometheus_rule_has_a_title_and_a_fix():
    """deploy/k8s/monitoring/rules.yml and portal/alerts.py name the same rules."""
    import re
    from pathlib import Path

    text = (Path(__file__).parents[2] / "deploy/k8s/monitoring/rules.yml").read_text()
    names = set(re.findall(r"^\s*- alert: (\w+)$", text, re.M))
    assert len(names) == 11
    assert names == set(alerts.PROM_TITLES) == set(alerts.PROM_FIXES)
    assert all(len(alerts.PROM + n) <= 40 for n in names)  # alert_states.key
