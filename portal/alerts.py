"""Alerts (#334): problems the service notices about itself, emailed to every super admin and shown on Attention.

- queue_stuck: documents are waiting and none has started or finished for `alert_queue_minutes`. A queue that's
  busy but moving never is: no document runs past the engine's time limit, so something finishes at least that
  often. Documents waiting on purpose (a paused service, tomorrow's daily limit, the person's confirmation) don't
  count.
- worker_stopped: the portal worker hasn't completed a pass for `alert_worker_minutes` (on 16 October every pass
  failed on one document, unnoticed).
- no_engine_workers: the engine has had no workers, or couldn't be reached, for `alert_engine_minutes`.
- backup_stale: only where the cluster runs the nightly backup (PORTAL_BACKUP_EXPECTED, #333): none has finished
  for `alert_backup_hours`. Every way it can fail (the share missing, a dump failing, the job not running) ends
  there.
- monitoring_down: only where the deployment has Prometheus (PORTAL_PROMETHEUS_URL, #397): it hasn't answered for
  `alert_engine_minutes`.
- siem_behind: only with the audit log sent to a SIEM (PORTAL_SIEM_MODE, #456): an event has waited more than an
  hour to be sent (the collector away, or refusing it).
- prom:<name>: an alert Prometheus's rules raised (deploy/k8s/monitoring/rules.yml): a pod crashing or restarting,
  a volume filling, memory near a pod's limit, the engine's queue full for long. They wait their rule's `for:`
  before firing, so they're told at once. One row per rule however many pods it fires for.

The portal worker evaluates them about once a minute, in a step of its own, so a pass that fails doesn't stop the
alert about it. One email when a problem starts, a reminder every `alert_repeat_hours`, one when it clears. When the
worker itself is down nothing can email: /api/v1/status answers 503 for an outside uptime check to catch.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from portal import outbox, queues, settings_store
from portal.config import get_portal_settings
from portal.mail import Mail
from portal.models import AlertState, Job, RoleAssignment, WorkerBeat

log = logging.getLogger(__name__)

WORKER = "portal-worker"
BACKUP = "backup"  # the last nightly backup that finished, or when backups were first expected
ASKED_BY_WORKER = {"no_engine_workers", "scanner_down", "scanner_signatures_old"}  # found only by the worker's own
# checks; the rest anyone can work out
PROM = "prom:"
TITLES = {
    "scanner_down": "The malware scanner isn't answering: uploads are refused",
    "scanner_signatures_old": "The malware scanner's signatures are out of date",
    "queue_stuck": "The document queue isn't moving",
    "worker_stopped": "The portal's background worker has stopped working",
    "no_engine_workers": "The engine has no workers",
    "backup_stale": "The nightly backup hasn't finished",
    "monitoring_down": "Monitoring (Prometheus) isn't answering",
    "siem_behind": "The audit log isn't reaching the SIEM",
}
FIXES = {
    "scanner_down": "Every upload is refused while it's down (#455). Look at the clamav pod: kubectl -n NS get pods -l "
                    "app=clamav, and its log (kubectl -n NS logs deploy/clamav -c clamd). It needs about 2 GB of memory "
                    "and a minute to load its signatures after a restart.",
    "scanner_signatures_old": "freshclam, beside clamd in the clamav pod, isn't getting updates: kubectl -n NS logs "
                              "deploy/clamav -c freshclam. With EGRESS_PROXY=enforce, database.clamav.net must be "
                              "reachable through the proxy; ClamAV's mirror also refuses a client that asks too often.",
    "queue_stuck": "Look at Administration > Queues: a document in progress may be stuck (Restart it), or the engine's "
                   "workers may be down (kubectl -n NS get pods; logs of deploy/engine-worker).",
    "worker_stopped": "Look at the portal worker's log (kubectl -n NS logs deploy/portal-worker): it says why its passes "
                      "fail. Its liveness probe restarts it after about 12 minutes.",
    "no_engine_workers": "Look at the engine's pods (kubectl -n NS get pods -l app=engine-worker) and their logs; "
                         "documents wait until a worker is back.",
    "backup_stale": "Look at the backup job (kubectl -n NS get jobs -l app=backup; logs of its latest pod): the NAS "
                    "share may be unreachable or full. docs/operations/backups-and-migrations.md has the steps.",
    "monitoring_down": "Look at Prometheus's pod (kubectl -n NS get pods -l app=prometheus; its logs): its volume may "
                       "be full (PROMETHEUS_SIZE, PROMETHEUS_RETENTION in deploy.env). Until it's back, the alerts "
                       "below from Prometheus and the System page's charts aren't available; the portal's own alerts "
                       "still are.",
    "siem_behind": "Administration > System shows the last error. Check the collector is up and its address, port, "
                   "certificate and token (PORTAL_SIEM_* in portal.env; docs/operations/siem.md). Nothing is lost: the "
                   "export carries on from where it stopped.",
}
# Prometheus's rules (deploy/k8s/monitoring/rules.yml; a test keeps the two in step). A rule not named here still
# alerts, with its own summary and description.
PROM_TITLES = {
    "PodCrashLooping": "A pod keeps crashing",
    "PodRestarting": "A pod has restarted several times in the last hour",
    "PodNotReady": "A pod hasn't been ready for 15 minutes",
    "DeploymentShort": "A service is running fewer pods than it should",
    "TargetDown": "Prometheus can't read a service's metrics",
    "MemoryNearLimit": "A pod is near its memory limit",
    "VolumeFilling": "A volume is nearly full",
    "RedisDown": "Redis (the engine's queue) isn't answering",
    "PostgresDown": "The database isn't answering",
    "EngineQueueWaiting": "Documents have waited 30 minutes for an engine worker",
    "PortalErrors": "The portal is answering with errors",
}
PROM_FIXES = {
    "PodCrashLooping": "Its log says why: kubectl -n NS logs POD --previous. Administration > System shows the pods.",
    "PodRestarting": "kubectl -n NS describe pod POD gives the reason for the last restart (OOMKilled: raise its "
                     "memory limit in deploy.env; a failed liveness probe: its log).",
    "PodNotReady": "kubectl -n NS describe pod POD: it may be waiting for a node with room, its volume, or its image.",
    "DeploymentShort": "kubectl -n NS get pods: the missing ones may be pending (no room on the nodes) or crashing.",
    "TargetDown": "The pod may be starting or stuck; kubectl -n NS get pods and its log. A pod that's gone on purpose "
                  "(scaled to none) isn't alerted.",
    "MemoryNearLimit": "A pod over its limit is restarted mid-document. Raise its limit in deploy.env (e.g. "
                       "WORKER_MEMORY_LIMIT) or take fewer documents at once (WORKER_CONCURRENCY).",
    "VolumeFilling": "Grafana's volumes dashboard shows which. The job folder: old jobs are cleared on their schedule; "
                     "the database or file store: grow the volume (STORAGE_SIZE settings in deploy.env).",
    "RedisDown": "kubectl -n NS logs statefulset/redis. Documents wait until it's back.",
    "PostgresDown": "kubectl -n NS logs statefulset/postgres. Nothing works without it.",
    "EngineQueueWaiting": "Every worker is busy. With scaling on, the engine may be at ENGINE_WORKERS_MAX: raise it if "
                          "the nodes have room (Administration > System shows them).",
    "PortalErrors": "kubectl -n NS logs deploy/portal-api: the errors are logged with their cause.",
}


def title(key: str) -> str:
    if key.startswith(PROM):
        return PROM_TITLES.get(key[len(PROM):], key[len(PROM):])
    return TITLES.get(key, key)


def fix(key: str) -> str | None:
    if key.startswith(PROM):
        return PROM_FIXES.get(key[len(PROM):], "Open Grafana from Administration > System for more.")
    return FIXES.get(key)


def ours(key: str) -> bool:
    """A service alert (not a group's pool, which pool_alerts keeps in the same table)."""
    return key in TITLES or key.startswith(PROM)


def prom_conditions(firing: list[dict]) -> dict[str, str]:
    """Prometheus's firing alerts as alert rows: by rule, the descriptions of up to three of what it fired for."""
    from portal.prometheus import grouped
    out: dict[str, str] = {}
    for name, items in grouped(firing).items():
        lines = sorted({(a.get("annotations") or {}).get("description")
                        or (a.get("annotations") or {}).get("summary") or name for a in items})
        more = f" And {len(lines) - 3} more." if len(lines) > 3 else ""
        out[(PROM + name)[:40]] = " ".join(lines[:3]) + more
    return out


def _now() -> datetime:
    return datetime.now(timezone.utc)


def beat(db: Session, name: str = WORKER, now: datetime | None = None) -> None:
    """A completed pass: kept for the API and the alerts to see."""
    row = db.get(WorkerBeat, name)
    if row is None:
        db.add(WorkerBeat(name=name, at=now or _now()))
    else:
        row.at = now or _now()


def _waiting(db: Session) -> int:
    """Documents that should be moving: sent to the engine, or queued and not waiting on purpose."""
    pauses = queues.paused(db)
    n = db.scalar(select(func.count()).select_from(Job).where(Job.status == "submitted")) or 0
    for j in db.scalars(select(Job).where(Job.status == "queued")):
        if (j.progress or {}).get("stage") == "waiting_daily_cap" or queues.paused_services(j, pauses):
            continue
        n += 1
    return n


def conditions(db: Session, engine_asked: bool = False, engine_workers: int | None = None,
               now: datetime | None = None, prom_asked: bool = False,
               prom_firing: list[dict] | None = None, scanner: dict | None = None) -> dict[str, str]:
    """What's wrong right now, by key, with a sentence on each. With `engine_asked`, `engine_workers` is how many
    documents the engine can take at once (None: it couldn't be reached). With `prom_asked`, `prom_firing` is what
    Prometheus has firing (None: it couldn't be reached). `scanner`: the malware scanner as the worker found it, when
    scanning is on (#455): {"error": ...} when it didn't answer, else {"version": ..., "signatures": datetime}."""
    rs = settings_store.load(db)
    now = now or _now()
    out: dict[str, str] = {}
    waiting = _waiting(db)
    if waiting:
        since = now - timedelta(minutes=rs.alert_queue_minutes)
        last = max([t for t in (db.scalar(select(func.max(Job.submitted_at))), db.scalar(select(func.max(Job.finished_at))))
                    if t is not None], default=None)
        if last is None or last < since:
            when = f"since {last:%Y-%m-%d %H:%M} UTC" if last else "yet"
            out["queue_stuck"] = (f"{waiting} document{'s' if waiting != 1 else ''} waiting, and none has started or "
                                  f"finished {when} (more than {rs.alert_queue_minutes} minutes).")
    row = db.get(WorkerBeat, WORKER)
    if row is not None and row.at < now - timedelta(minutes=rs.alert_worker_minutes):
        out["worker_stopped"] = (f"No completed pass since {row.at:%Y-%m-%d %H:%M} UTC (more than "
                                 f"{rs.alert_worker_minutes} minutes): documents, saves and emails aren't being handled.")
    if get_portal_settings().backup_expected:
        last = db.get(WorkerBeat, BACKUP)
        if last is not None and last.at < now - timedelta(hours=rs.alert_backup_hours):
            out["backup_stale"] = (f"No nightly backup has finished since {last.at:%Y-%m-%d %H:%M} UTC (more than "
                                   f"{rs.alert_backup_hours} hours).")
    from portal import siem  # the audit log to the campus SIEM (#456)
    late = siem.behind(db, now)
    if late is not None and late > siem.BEHIND:
        err = siem.state(db).get("last_error")
        out["siem_behind"] = (f"Audit events have waited {int(late.total_seconds() // 60)} minutes to be sent "
                              f"({siem.mode()})" + (f": {err}" if err else "") + ".")
    if scanner is not None:
        if scanner.get("error"):
            out["scanner_down"] = f"clamd didn't answer: {scanner['error']}."
        elif scanner.get("signatures") is not None:
            days = get_portal_settings().malware_signatures_days
            if scanner["signatures"] < now - timedelta(days=days):
                out["scanner_signatures_old"] = (f"Its signatures were published {scanner['signatures']:%Y-%m-%d %H:%M} "
                                                 f"UTC, more than {days} days ago ({scanner.get('version')}).")
    if engine_asked and not engine_workers:
        out["no_engine_workers"] = ("The engine couldn't be reached." if engine_workers is None
                                    else "The engine reports no workers to process documents.")
    if prom_asked:
        if prom_firing is None:
            out["monitoring_down"] = "Prometheus couldn't be reached."
        else:
            out.update(prom_conditions(prom_firing))
    return out


def _grace(key: str, rs) -> timedelta:
    """How long a problem must last before anyone is told: the queue's and the worker's tests already wait. Old
    malware signatures wait an hour: a scanner just started has the ones its image was built with, usually days old,
    until freshclam's first update a minute or so later."""
    if key == "scanner_signatures_old":
        return timedelta(hours=1)
    waits = ("no_engine_workers", "monitoring_down", "scanner_down")
    return timedelta(minutes=rs.alert_engine_minutes) if key in waits else timedelta(0)


def active(db: Session, now: datetime | None = None) -> list[AlertState]:
    """The problems super admins have been told about (past their grace), oldest first."""
    rs = settings_store.load(db)
    now = now or _now()
    rows = db.scalars(select(AlertState).order_by(AlertState.started_at)).all()
    return [r for r in rows if ours(r.key) and now - r.started_at >= _grace(r.key, rs)]


def _super_admins(db: Session) -> list[str]:
    rows = db.scalars(select(RoleAssignment).where(RoleAssignment.role == "super_admin"))
    return sorted({r.user.email for r in rows if r.user.status == "active"})


def mail_super_admins(db: Session, key: str, subject: str, text: str) -> None:
    rs = settings_store.load(db)
    footer = f"\n\n{rs.service_name}" + (f", {rs.organization_name}" if rs.organization_name else "") + "\n"
    outbox.queue(db, [Mail(to=to, subject=subject, text=text + footer) for to in _super_admins(db)],
                 rs.mail_from_name, "alert")


def evaluate(db: Session, engine_asked: bool = False, engine_workers: int | None = None,
             now: datetime | None = None, prom_asked: bool = False,
             prom_firing: list[dict] | None = None, scanner: dict | None = None) -> list[str]:
    """Bring the alert rows up to date and queue their emails. Returns the keys emailed about this time."""
    rs = settings_store.load(db)
    now = now or _now()
    if get_portal_settings().backup_expected and db.get(WorkerBeat, BACKUP) is None:
        beat(db, BACKUP, now)  # expected from now: no backup within the time is then alerted like a stale one
    found = conditions(db, engine_asked, engine_workers, now, prom_asked, prom_firing, scanner)
    rows = {r.key: r for r in db.scalars(select(AlertState)) if ours(r.key)}  # not the pools' (pool_alerts)
    sent: list[str] = []
    if not engine_asked:  # not asked this time: keep what was known
        found.update({k: rows[k].detail for k in ("no_engine_workers",) if k in rows})
    from portal import malware
    if scanner is None and malware.enabled():  # scanning on, but not asked this time
        found.update({k: rows[k].detail for k in ("scanner_down", "scanner_signatures_old") if k in rows})
    if prom_asked and prom_firing is None:  # Prometheus away: what it last said still stands, until it's back
        found.update({k: r.detail for k, r in rows.items() if k.startswith(PROM)})
    for key, detail in found.items():
        row = rows.get(key)
        if row is None:
            row = AlertState(key=key, detail=detail, started_at=now)
            db.add(row)
        row.detail = detail
        if now - row.started_at < _grace(key, rs):
            continue
        due = row.notified_at is None or now - row.notified_at >= timedelta(hours=rs.alert_repeat_hours)
        if due:
            again = row.notified_at is not None
            mail_super_admins(db, key, f"{'Still: ' if again else 'Alert: '}{title(key)}",
                  f"{detail}\n\nWhat to do: {fix(key)}\n\nStarted {row.started_at:%Y-%m-%d %H:%M} UTC. You'll be "
                  f"reminded every {rs.alert_repeat_hours} hour(s) while it lasts, and told when it clears. It's also on "
                  "Administration > Attention.")
            row.notified_at = now
            sent.append(key)
    for key, row in rows.items():
        if key not in found:
            if row.notified_at is not None:
                mail_super_admins(db, key, f"Cleared: {title(key)}",
                      f"This has cleared: {row.detail}\n\nIt started {row.started_at:%Y-%m-%d %H:%M} UTC.")
            db.delete(row)
    return sent
