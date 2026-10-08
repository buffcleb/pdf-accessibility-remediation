"""The email outbox: notifications are queued here and the worker sends them, so they can be seen, paused and
retried. Sign-in codes and test emails don't come through here; they're sent at once and can't be paused.

Temporary failures (timeouts, throttling, server errors) are tried again with a growing wait, up to
PORTAL_MAIL_ATTEMPTS; anything else, or the last attempt, leaves the email failed for a super admin to retry.
"""
from __future__ import annotations

import base64
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from portal.config import get_portal_settings
from portal.mail import Mail, MailError, get_mailer
from portal.models import MailOutbox

log = logging.getLogger(__name__)

MAIL_QUEUE = "mail"
BATCH = 50
KEEP_SENT = timedelta(days=30)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def queue(db: Session, mails: list[Mail], from_name: str | None, kind: str, about: str | None = None) -> None:
    """Add emails to the outbox; they go out with the caller's commit and the worker's next pass. Notifications
    follow each person's choice: at once, held for their daily summary, or not sent."""
    from portal import notifications  # it reads settings and users; this module stays light for the mailer
    for m in mails:
        status = notifications.status_for(db, m.to, kind)
        if status is None:
            continue
        # About something they might be looking at: wait a moment, so the app can say it's open
        wait = notifications.SEEN_DELAY if about else timedelta(0)
        db.add(MailOutbox(kind=kind, to_addr=m.to, subject=m.subject, body=m.text, html=m.html, from_name=from_name,
                          attachments=encode_attachments(m), status=status, about=about, next_try_at=_now() + wait))


def encode_attachments(m: Mail) -> list | None:
    return [{"name": n, "type": t, "data": base64.b64encode(d).decode()} for n, t, d in m.attachments] or None


def decode_attachments(stored: list | None) -> list[tuple[str, str, bytes]]:
    return [(a["name"], a["type"], base64.b64decode(a["data"])) for a in stored or []]


def _retry_wait(attempts: int) -> timedelta:
    return timedelta(seconds=min(900, 30 * 2 ** (attempts - 1)))


def send_queued(db: Session) -> int:
    """Worker: send what's due, unless email is paused. Returns how many were sent."""
    from portal import queues  # queues imports this module
    if queues.is_paused(db, MAIL_QUEUE):
        return 0
    rows = db.scalars(select(MailOutbox).where(MailOutbox.status == "queued", MailOutbox.next_try_at <= _now())
                      .order_by(MailOutbox.created_at).limit(BATCH).with_for_update(skip_locked=True)).all()
    from portal import notifications
    sent = 0
    tries = get_portal_settings().mail_attempts
    for row in rows:
        if row.attempts == 0 and notifications.seen(db, row.to_addr, row.about, row.created_at):
            row.status, row.finished_at = "seen", _now()  # they saw it in the portal
            db.commit()
            continue
        row.attempts += 1
        try:
            # One try here; the outbox does the waiting between tries, so the worker never sleeps on mail
            get_mailer().deliver(Mail(to=row.to_addr, subject=row.subject, text=row.body, html=row.html,
                                      attachments=decode_attachments(row.attachments)), row.from_name, attempts=1)
        except MailError as e:
            row.last_error = str(e)[:1000]
            if e.retryable and row.attempts < tries:
                row.next_try_at = _now() + _retry_wait(row.attempts)
            else:
                row.status, row.finished_at = "failed", _now()
                log.error("email to %s failed: %s", row.to_addr, e)
        except Exception as e:  # noqa: BLE001 - a broken email never stops the others
            row.last_error = str(e)[:1000]
            row.status, row.finished_at = "failed", _now()
            log.exception("email to %s failed", row.to_addr)
        else:
            row.status, row.finished_at, row.last_error = "sent", _now(), None
            sent += 1
        db.commit()
    return sent


def purge(db: Session, record_retention_days: int) -> int:
    """Sent, canceled and combined (into a daily summary) emails go after 30 days; failed ones with the other
    records."""
    now = _now()
    n = db.execute(delete(MailOutbox).where(MailOutbox.status.in_(("sent", "canceled", "combined", "seen")),
                                            MailOutbox.created_at < now - KEEP_SENT)).rowcount
    n += db.execute(delete(MailOutbox).where(MailOutbox.status == "failed",
                                             MailOutbox.created_at < now - timedelta(days=record_retention_days))).rowcount
    return n
