"""The audit log to the campus SIEM (#456, docs/operations/siem.md).

Each audit event goes as one JSON object: its id, time, action, outcome, actor (id and email), group (id and name),
target, client IP, browser and details. Never a document's content: the audit log holds none.

The portal worker sends them in order of id, in batches, from where it left off (kept in the settings table as
`siem.position`), by PORTAL_SIEM_MODE:
- syslog: RFC 5424 messages over TCP, framed by octet counting (RFC 5425), with TLS unless PORTAL_SIEM_SYSLOG_TLS is
  off. Facility 13 (log audit); severity warning for a security alert, notice for anything that didn't succeed, info
  otherwise; the action as the message id; the JSON as the message.
- https: one POST per batch, JSON lines with a Bearer token, or Splunk's HTTP Event Collector format with its
  "Splunk" token.

A batch that fails is sent again, after 30 seconds, then a minute, doubling up to 15 minutes; the position only moves
once a batch is accepted, so nothing is dropped (a collector may see a batch twice after a failure part way: the
event id tells them apart). Events are sent once they're two minutes old, so one whose transaction commits a moment
after a later one's isn't passed over. More than an hour behind is a service alert (portal/alerts.py).

For a SIEM that polls instead, GET /api/v1/admin/audit/export (portal/routers/audit_export.py) gives the same objects.
"""
from __future__ import annotations

import json
import logging
import socket
import ssl
from datetime import datetime, timedelta, timezone
from typing import Protocol
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from portal.config import get_portal_settings
from portal.models import AuditEvent, Group, Setting

log = logging.getLogger(__name__)

STATE_KEY = "siem.position"
LAG = timedelta(minutes=2)
BEHIND = timedelta(hours=1)  # alerted past this
PASSES = 5  # batches per worker pass, to catch up
LOCK = 456_001  # an advisory lock, so two workers never send the same batch
APP = "pdf-remediation"
FACILITY = 13  # log audit


def mode() -> str:
    m = (get_portal_settings().siem_mode or "off").strip().lower()
    return m if m in ("syslog", "https") else "off"


def as_json(ev: AuditEvent, group_name: str | None = None) -> dict:
    return {
        "id": ev.id,
        "time": ev.created_at.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "service": APP,
        "action": ev.action,
        "outcome": ev.outcome,
        "actor": {"id": str(ev.actor_id) if ev.actor_id else None, "email": ev.actor_email},
        "group": {"id": str(ev.group_id) if ev.group_id else None, "name": group_name},
        "target": {"type": ev.target_type, "id": ev.target_id},
        "ip": str(ev.ip) if ev.ip else None,
        "user_agent": ev.user_agent,
        "details": ev.details or {},
    }


def events(db: Session, after: int, limit: int, now: datetime | None = None) -> list[dict]:
    """Events after this id that are old enough to send, oldest first, as JSON objects."""
    now = now or datetime.now(timezone.utc)
    rows = db.execute(select(AuditEvent, Group.name).outerjoin(Group, Group.id == AuditEvent.group_id)
                      .where(AuditEvent.id > after, AuditEvent.created_at < now - LAG)
                      .order_by(AuditEvent.id).limit(limit)).all()
    return [as_json(ev, name) for ev, name in rows]


def _severity(e: dict) -> int:
    if e["action"] == "security.alert":
        return 4
    return 6 if e["outcome"] in ("success", "") else 5


def _msgid(action: str) -> str:
    out = "".join(c if 33 <= ord(c) <= 126 else "_" for c in action)[:32]
    return out or "-"


def syslog_message(e: dict, hostname: str) -> bytes:
    """One event as an RFC 5424 message: <PRI>1 TIMESTAMP HOSTNAME APP-NAME PROCID MSGID SD MSG."""
    host = "".join(c for c in hostname if 33 <= ord(c) <= 126)[:255] or "-"
    head = f"<{FACILITY * 8 + _severity(e)}>1 {e['time']} {host} {APP} - {_msgid(e['action'])} -"
    return f"{head} {json.dumps(e, separators=(',', ':'), ensure_ascii=False)}".encode()


def syslog_frame(message: bytes) -> bytes:
    """Octet counting (RFC 5425 / 6587): the length, a space, the message."""
    return str(len(message)).encode() + b" " + message


class Sink(Protocol):
    def send(self, batch: list[dict]) -> None: ...


def _hostname() -> str:
    return urlsplit(get_portal_settings().app_url).hostname or socket.gethostname()


class SyslogSink:
    def __init__(self, host: str, port: int, tls: bool = True, ca: str = "", timeout: float = 15):
        self.host, self.port, self.tls, self.ca, self.timeout = host, port, tls, ca.replace("\\n", "\n"), timeout

    def send(self, batch: list[dict]) -> None:
        if not self.host:
            raise RuntimeError("PORTAL_SIEM_SYSLOG_HOST isn't set")
        data = b"".join(syslog_frame(syslog_message(e, _hostname())) for e in batch)
        with socket.create_connection((self.host, self.port), timeout=self.timeout) as raw:
            if self.tls:
                ctx = ssl.create_default_context(cadata=self.ca or None)
                ctx.minimum_version = ssl.TLSVersion.TLSv1_2
                with ctx.wrap_socket(raw, server_hostname=self.host) as conn:
                    conn.sendall(data)
            else:
                raw.sendall(data)


class HttpsSink:
    def __init__(self, url: str, token: str, fmt: str = "lines", timeout: float = 30):
        self.url, self.token, self.fmt, self.timeout = url, token, fmt, timeout

    def send(self, batch: list[dict]) -> None:
        if not self.url:
            raise RuntimeError("PORTAL_SIEM_HTTPS_URL isn't set")
        if self.fmt == "splunk":
            body = "".join(json.dumps({"time": datetime.fromisoformat(e["time"].replace("Z", "+00:00")).timestamp(),
                                       "host": _hostname(), "source": APP, "sourcetype": "_json", "event": e},
                                      separators=(",", ":")) for e in batch)
            headers = {"Authorization": f"Splunk {self.token}", "Content-Type": "application/json"}
        else:
            body = "".join(json.dumps(e, separators=(",", ":")) + "\n" for e in batch)
            headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/x-ndjson"}
        # Through the egress proxy when there is one (HTTPS_PROXY, #453)
        r = httpx.post(self.url, content=body.encode(), headers=headers, timeout=self.timeout)
        r.raise_for_status()


def get_sink() -> Sink | None:
    s = get_portal_settings()
    if mode() == "syslog":
        return SyslogSink(s.siem_syslog_host, s.siem_syslog_port, s.siem_syslog_tls, s.siem_syslog_ca)
    if mode() == "https":
        return HttpsSink(s.siem_https_url, s.siem_https_token, s.siem_https_format)
    return None


def state(db: Session) -> dict:
    row = db.get(Setting, STATE_KEY)
    return dict(row.value) if row else {"after": 0}


def _save(db: Session, st: dict) -> None:
    row = db.get(Setting, STATE_KEY)
    if row is None:
        db.add(Setting(key=STATE_KEY, value=st))
    else:
        row.value = st


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).isoformat()


def export(db: Session, sink: Sink | None = None, now: datetime | None = None) -> int:
    """Send what's waiting, up to PASSES batches. Commits after each batch. Returns how many events were sent."""
    sink = sink or get_sink()
    if sink is None:
        return 0
    now = now or datetime.now(timezone.utc)
    if not db.scalar(text("select pg_try_advisory_xact_lock(:k)"), {"k": LOCK}):
        return 0  # another worker is sending
    st = state(db)
    if st.get("retry_at") and now < datetime.fromisoformat(st["retry_at"]):
        return 0
    sent = 0
    for _ in range(PASSES):
        batch = events(db, int(st.get("after") or 0), max(1, get_portal_settings().siem_batch), now)
        if not batch:
            break
        try:
            sink.send(batch)
        except Exception as e:  # noqa: BLE001 - kept, retried later; the position doesn't move
            failures = int(st.get("failures") or 0) + 1
            wait = min(30 * 2 ** (failures - 1), 900)
            st.update(failures=failures, retry_at=_iso(now + timedelta(seconds=wait)),
                      last_error=f"{type(e).__name__}: {e}"[:300], last_error_at=_iso(now))
            log.warning("SIEM export failed (%s), trying again in %s s: %s", failures, wait, e)
            break
        st.update(after=batch[-1]["id"], sent_up_to=batch[-1]["time"], last_success=_iso(now), failures=0,
                  retry_at=None)
        sent += len(batch)
        _save(db, st)
        db.commit()
        if not db.scalar(text("select pg_try_advisory_xact_lock(:k)"), {"k": LOCK}):
            return sent
        if len(batch) < max(1, get_portal_settings().siem_batch):
            break
    _save(db, st)
    db.commit()
    return sent


def behind(db: Session, now: datetime | None = None) -> timedelta | None:
    """How long the oldest event not yet sent has waited (None: nothing waiting, or the export is off)."""
    if mode() == "off":
        return None
    now = now or datetime.now(timezone.utc)
    oldest = db.scalar(select(AuditEvent.created_at).where(AuditEvent.id > int(state(db).get("after") or 0))
                       .order_by(AuditEvent.id).limit(1))
    return now - oldest if oldest is not None else None


def status(db: Session, now: datetime | None = None) -> dict:
    """For Administration > System."""
    s = get_portal_settings()
    m = mode()
    st = state(db)
    to = {"syslog": f"{s.siem_syslog_host}:{s.siem_syslog_port}" + ("" if s.siem_syslog_tls else " (no TLS)"),
          "https": urlsplit(s.siem_https_url).hostname or ""}.get(m)
    late = behind(db, now)
    return {"mode": m, "destination": to, "pull": bool(s.siem_pull_token), "sent_up_to": st.get("sent_up_to"),
            "last_success": st.get("last_success"), "last_error": st.get("last_error"),
            "last_error_at": st.get("last_error_at"), "failures": int(st.get("failures") or 0),
            "behind_seconds": int(late.total_seconds()) if late else 0}
