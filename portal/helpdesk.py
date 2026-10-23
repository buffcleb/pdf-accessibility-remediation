"""Help requests: people ask for help with a remediated document; super admins work through them.

Who sees what:
- the person who asked, the admins of their group, and super admins can read a request and write messages;
- internal notes are for super admins only;
- only super admins take a request or change its status.

Emails go to the other side when someone writes or the status changes. Several messages within
NOTIFY_WINDOW go out as one email: the first is sent at once, later ones wait for the worker.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import UploadFile
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from portal import outbox, settings_store
from portal.config import get_portal_settings
from portal.errors import ApiError
from portal.mail import Mail
from portal.models import HelpAttachment, HelpMessage, HelpRequest, RoleAssignment, User

# A request waiting for support: new, or in progress with the person's last word unanswered. And since when it's
# waited (the help queue's "needs us", and Needs attention's item, #538)
NEEDS_STAFF = or_(HelpRequest.status == "open", and_(
    HelpRequest.status == "in_progress", HelpRequest.person_wrote_at.is_not(None),
    or_(HelpRequest.staff_wrote_at.is_(None), HelpRequest.person_wrote_at > HelpRequest.staff_wrote_at)))
WAITING_SINCE = case((NEEDS_STAFF, func.greatest(HelpRequest.person_wrote_at, HelpRequest.created_at)),
                     else_=func.coalesce(HelpRequest.status_changed_at, HelpRequest.updated_at))
from portal.storage import get_storage, safe_name

log = logging.getLogger(__name__)

NOTIFY_WINDOW = timedelta(minutes=10)
MAX_FILES_PER_MESSAGE = 5
MAX_BODY = 5000

KIND_LABELS = {
    "alt_text": "Alt text (descriptions of images)",
    "reading_order": "Reading order",
    "tables": "Tables",
    "headings": "Headings",
    "math": "Math and notation",
    "visual": "Looks different from the original",
    "other": "Something else",
    "failed": "The document couldn't be processed",
}
# Failures nobody needs to look into: someone stopped the document on purpose
NO_REQUEST_FOR = {"stopped", "canceled"}
SERVICE = "The service"
STATUS_LABELS = {
    "open": "Open",
    "in_progress": "In progress",
    "waiting": "Waiting on the person",
    "resolved": "Resolved",
    "closed": "Closed",
}
# What the person sees for each status
PERSON_STATUS = {
    "open": "Received",
    "in_progress": "Being looked at",
    "waiting": "We need something from you",
    "resolved": "Resolved",
    "closed": "Closed",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- access ----------------------------------------------------------------------------------------

def role_of(user: User, req: HelpRequest) -> str | None:
    """How this user takes part in the request, or None if they can't see it."""
    if user.is_super_admin:
        return "super_admin"
    if req.user_id == user.id:
        return "person"
    if req.group_id is not None and req.group_id in user.admin_group_ids():
        return "group_admin"
    return None


def visible_messages(req: HelpRequest, role: str) -> list[HelpMessage]:
    return [m for m in req.messages if m.kind != "note" or role == "super_admin"]


# --- attachments -----------------------------------------------------------------------------------

def _sniff(head: bytes) -> str | None:
    """The type by content, not by name: PDF, PNG, JPEG or UTF-8 text."""
    if head.startswith(b"%PDF-"):
        return "application/pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if b"\x00" in head:
        return None
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as e:  # a multi-byte character cut at the end of the sample is fine
        if e.start < len(head) - 4:
            return None
    return "text/plain; charset=utf-8"


def read_attachments(files: list[UploadFile], already_bytes: int, max_file_mb: int, max_request_mb: int
                     ) -> list[tuple[str, str, bytes]]:
    """Check and read uploads: (name, content type, data). Raises with a message naming the file."""
    files = [f for f in files if f is not None and (f.filename or "").strip()]
    if len(files) > MAX_FILES_PER_MESSAGE:
        raise ApiError(422, "too_many_files", f"Attach up to {MAX_FILES_PER_MESSAGE} files at a time.")
    out, total = [], already_bytes
    for f in files:
        data = f.file.read(max_file_mb * 1024 * 1024 + 1)
        name = f.filename or "file"
        if len(data) > max_file_mb * 1024 * 1024:
            raise ApiError(422, "file_too_large", f"{name} is larger than {max_file_mb} MB.")
        if not data:
            raise ApiError(422, "empty_file", f"{name} is empty.")
        ctype = _sniff(data[:4096])
        if ctype is None:
            raise ApiError(422, "file_type", f"{name} isn't a PDF, PNG, JPEG or plain-text file.")
        total += len(data)
        if total > max_request_mb * 1024 * 1024:
            raise ApiError(422, "request_too_large", f"A help request can hold up to {max_request_mb} MB of files.")
        out.append((name, ctype, data))
    return out


def attachments_bytes(db: Session, req: HelpRequest) -> int:
    from sqlalchemy import func
    return db.scalar(select(func.coalesce(func.sum(HelpAttachment.size_bytes), 0))
                     .where(HelpAttachment.request_id == req.id)) or 0


def store_attachments(db: Session, req: HelpRequest, msg: HelpMessage, files: list[tuple[str, str, bytes]]) -> None:
    storage = get_storage()
    for name, ctype, data in files:
        att_id = uuid.uuid4()
        key = f"{prefix(req)}{att_id}/{safe_name(name, 'file')}"
        storage.put_bytes(key, data, ctype)
        db.add(HelpAttachment(id=att_id, message_id=msg.id, request_id=req.id, s3_key=key, name=name[:255],
                              content_type=ctype, size_bytes=len(data)))


def prefix(req: HelpRequest) -> str:
    """Attachments live apart from the document's files, so a request saved past retention keeps them."""
    return f"help/{req.group_id or 'no-group'}/{req.id}/"


def delete_request(db: Session, req: HelpRequest) -> int:
    n = get_storage().delete_prefix(prefix(req))
    db.delete(req)
    return n


# --- writing ---------------------------------------------------------------------------------------

def add_message(db: Session, req: HelpRequest, author: User, role: str, kind: str, body: str) -> HelpMessage:
    # Load the existing messages first, then add through the relationship: adding to the session first
    # would let the lazy load flush and fetch the new message, and the append would list it twice.
    messages = req.messages
    msg = HelpMessage(kind=kind, author_id=author.id if author else None,
                      author_email=author.email if author else SERVICE, author_role=role, body=body)
    messages.append(msg)
    now = _now()
    req.updated_at = now
    if kind == "message":
        if role == "super_admin":
            req.staff_wrote_at = now
        else:
            req.person_wrote_at = now
    if author is not None and author.id == req.user_id:
        # What the person who asked writes or does isn't news to them, whatever their role: a super admin can ask
        # for help with their own document, and close the request themselves
        req.person_seen_at = now
    db.flush()
    return msg


def set_status(db: Session, req: HelpRequest, status: str, actor: User | None, role: str,
               event: str | None = None) -> HelpMessage | None:
    if status == req.status:
        return None
    req.status = status
    req.status_changed_at = _now()
    if status == "waiting":
        req.reminders_sent = 0
    req.finished_at = _now() if status in ("resolved", "closed") else None
    return add_message(db, req, actor, role, "event", event or f"status:{status}")


def reopen_until(req: HelpRequest, days: int) -> datetime | None:
    """Until when the person can reopen a finished request themselves."""
    return req.finished_at + timedelta(days=days) if req.finished_at and not req.active else None


def event_text(m: HelpMessage, for_person: bool) -> str | None:
    """Events are stored as codes (status:waiting, assigned, released) and worded for whoever reads them."""
    if m.body.startswith("status:"):
        status = m.body.split(":", 1)[1]
        if for_person:
            return f"Status: {PERSON_STATUS.get(status, status)}"
        by = f" ({m.author_email})" if m.author_role != "system" else ""
        return f"Status: {STATUS_LABELS.get(status, status)}{by}"
    if m.body == "assigned":
        return "Someone from support is looking at it" if for_person else f"{m.author_email} is handling it"
    if m.body == "released":
        return None if for_person else f"{m.author_email} let it go"
    if m.body == "reopened":
        return "Reopened" if for_person else f"Reopened by {m.author_email}"
    if m.body == "auto_closed":
        return ("Closed after 10 days without a reply. You can reopen it for a while if you still need help."
                if for_person else "Closed automatically after 10 days without a reply")
    if m.body.startswith("corrected:"):
        return None if for_person else f"Corrected file uploaded by {m.author_email}: {m.body.split(':', 1)[1]}"
    return m.body


# --- emails ----------------------------------------------------------------------------------------

def _super_admins(db: Session) -> list[str]:
    rows = db.scalars(select(RoleAssignment).where(RoleAssignment.role == "super_admin"))
    return sorted({r.user.email for r in rows if r.user.status == "active"})


def _staff_recipients(db: Session, req: HelpRequest) -> list[str]:
    """The super admin handling it, or every super admin while nobody has taken it."""
    if req.assignee_id:
        u = db.get(User, req.assignee_id)
        if u is not None and u.status == "active":
            return [u.email]
    return _super_admins(db)


def _footer(db: Session) -> str:
    rs = settings_store.load(db)
    return f"\n{rs.service_name}" + (f", {rs.organization_name}" if rs.organization_name else "") + "\n"


def _url(path: str) -> str:
    return get_portal_settings().app_url.rstrip("/") + path


def queue_mail(db: Session, mails: list[Mail], req: HelpRequest | None = None) -> None:
    """Into the outbox; the worker sends them. About a request: not sent to someone who had it open."""
    outbox.queue(db, mails, settings_store.load(db).mail_from_name, "help", about=f"help:{req.id}" if req else None)


def failure_body(message: str | None, code: str | None) -> str:
    """What the request's first message says about a failure: what the person was told, and the error's name.
    The recorded detail is internal (file paths, engine messages), so it goes in a note for super admins."""
    lines = ["This document couldn't be processed, so a help request was opened for it.", "",
             f"What the person was told: {message or 'Remediation failed for this document.'}"]
    if code:
        lines.append(f"Error: {code}")
    return "\n".join(lines)


def open_for_failure(db: Session, job, code: str | None, detail: str | None) -> HelpRequest | None:
    """A help request for a document that failed, opened by the service itself (when the setting is on, and unless
    someone stopped the document on purpose or it already has a request). Super admins get the usual email."""
    if code in NO_REQUEST_FOR or not settings_store.load(db).help_on_failure or job.user is None:
        return None
    if db.scalar(select(HelpRequest).where(HelpRequest.job_id == job.id,
                                           HelpRequest.status.in_(("open", "in_progress", "waiting")))) is not None:
        return None
    req = HelpRequest(id=uuid.uuid4(), job_id=job.id, filename=job.filename, user_id=job.user_id, group_id=job.group_id,
                      kind="failed", status="open")
    db.add(req)
    db.flush()
    first = add_message(db, req, None, "system", "message", failure_body(job.error_message, code))
    if detail and detail.strip():
        add_message(db, req, None, "system", "note", f"Recorded with the failure: {detail.strip()[:1500]}")
    db.flush()
    db.refresh(req)
    queue_mail(db, new_request_mails(db, req, first, detail), req)
    req.staff_notified_at = _now()
    return req


def new_request_mails(db: Session, req: HelpRequest, first: HelpMessage, detail: str | None = None) -> list[Mail]:
    who = req.user.email
    group = req.group.name if req.group else "no group"
    if first.author_role == "system":
        text = (f"{req.filename}, submitted by {who} ({group}), couldn't be processed. A help request was opened for "
                f"it.\n\n{first.body}\n" + (f"Recorded with the failure: {detail.strip()[:1500]}\n" if detail else "")
                + f"\nOpen the request:\n{_url(f'/admin/help/{req.id}')}\n" + _footer(db))
        return [Mail(to=to, subject=f"Failed document: {req.filename}", text=text) for to in _super_admins(db)]
    text = (f"{who} ({group}) asked for help with {req.filename}.\n\n"
            f"Kind of problem: {KIND_LABELS[req.kind]}\n"
            + (f"Version: {req.version_label}\n" if req.version_label else "")
            + (f"Page: {req.page}\n" if req.page else "")
            + f"\n{first.body}\n\nOpen the request:\n{_url(f'/admin/help/{req.id}')}\n" + _footer(db))
    return [Mail(to=to, subject=f"Help request: {req.filename}", text=text) for to in _super_admins(db)]


def _digest(db: Session, req: HelpRequest, side: str, since: datetime | None) -> list[Mail]:
    """One email for the messages and status changes the other side wrote since `since`."""
    if side == "person":
        items = [m for m in req.messages if m.kind in ("message", "event") and m.author_id != req.user_id
                 and (since is None or m.created_at > since)]
        recipients = [req.user.email] if req.user.status == "active" else []
        link = _url(f"/jobs/{req.job_id}") if req.job_id else _url("/")
        subject = f"Update on your help request: {req.filename}"
        intro = f"There's an update on your help request about {req.filename}. It's now: {PERSON_STATUS[req.status]}."
    else:
        items = [m for m in req.messages if m.kind == "message" and m.author_role != "super_admin"
                 and (since is None or m.created_at > since)]
        recipients = _staff_recipients(db, req)
        link = _url(f"/admin/help/{req.id}")
        subject = f"Reply on help request: {req.filename}"
        intro = f"There's a reply on the help request about {req.filename} from {req.user.email}."
    if not items or not recipients:
        return []
    lines = []
    for m in items:
        if m.kind == "event":
            text = event_text(m, side == "person")
            if text:
                lines.append(text)
            continue
        lines.append(f"{m.author_email} wrote:\n{m.body}"
                     + (f"\n(attached: {', '.join(a.name for a in m.attachments)})" if m.attachments else ""))
    if not lines:
        return []
    body = "\n\n".join(lines)
    text = f"{intro}\n\n{body}\n\nOpen it:\n{link}\n" + _footer(db)
    return [Mail(to=to, subject=subject, text=text) for to in recipients]


def notify(db: Session, req: HelpRequest, side: str) -> None:
    """Tell one side about what's new: at once if nothing went to them in the last few minutes, otherwise the
    worker sends one email for the lot when the window ends."""
    now = _now()
    last = req.person_notified_at if side == "person" else req.staff_notified_at
    if last is not None and now - last < NOTIFY_WINDOW:
        setattr(req, f"{side}_notify_pending", True)
        return
    mails = _digest(db, req, side, last)
    setattr(req, f"{side}_notified_at", now)
    setattr(req, f"{side}_notify_pending", False)
    if mails:
        queue_mail(db, mails, req)


def send_pending(db: Session) -> int:
    """Worker: send the batched emails whose window has ended."""
    cutoff = _now() - NOTIFY_WINDOW
    sent = 0
    for side in ("person", "staff"):
        pending = getattr(HelpRequest, f"{side}_notify_pending")
        notified = getattr(HelpRequest, f"{side}_notified_at")
        for req in db.scalars(select(HelpRequest).where(pending.is_(True), notified <= cutoff)
                              .with_for_update(skip_locked=True).limit(200)):
            notify(db, req, side)
            sent += 1
    db.commit()
    return sent


def expire_with_documents(db: Session, job_ids: list[uuid.UUID]) -> tuple[int, int]:
    """Retention: requests about documents whose files were just deleted go too, unless saved."""
    if not job_ids:
        return 0, 0
    reqs = db.scalars(select(HelpRequest).where(HelpRequest.job_id.in_(job_ids), HelpRequest.saved.is_(False))).all()
    objects = sum(delete_request(db, r) for r in reqs)
    return len(reqs), objects


# --- waiting on the person -------------------------------------------------------------------------

REMIND_AFTER = (timedelta(days=3), timedelta(days=7))
CLOSE_AFTER = timedelta(days=10)


def remind_and_close(db: Session) -> dict:
    """Worker: requests waiting on the person get a reminder after 3 and 7 days without a reply, and are
    closed at 10 (the person can still reopen them for a while)."""
    now = _now()
    rs = settings_store.load(db)
    counts = {"reminded": 0, "closed": 0}
    waiting = db.scalars(select(HelpRequest).where(HelpRequest.status == "waiting", HelpRequest.saved.is_(False))
                         .with_for_update(skip_locked=True).limit(500)).all()
    for req in waiting:
        # A reply from them moves the request back to in progress, so anything still waiting hasn't heard back
        age = now - (req.status_changed_at or req.updated_at)
        link = _url(f"/jobs/{req.job_id}") if req.job_id else _url("/")
        if age >= CLOSE_AFTER:
            set_status(db, req, "closed", None, "system", event="auto_closed")
            text = (f"We didn't hear back about your help request for {req.filename}, so it's now closed.\n\n"
                    f"If you still need help, you can reopen it from the document's page for {rs.help_reopen_days} days:"
                    f"\n{link}\n" + _footer(db))
            mails = [Mail(to=req.user.email, subject=f"Your help request was closed: {req.filename}", text=text)]
            counts["closed"] += 1
        elif req.reminders_sent < len(REMIND_AFTER) and age >= REMIND_AFTER[req.reminders_sent]:
            req.reminders_sent += 1
            last = next((m for m in reversed(req.messages) if m.kind == "message" and m.author_role == "super_admin"), None)
            text = (f"We're waiting for your reply about {req.filename}."
                    + (f"\n\nOur last message:\n{last.body}" if last else "")
                    + f"\n\nReply here:\n{link}\n\nIf we don't hear back within {CLOSE_AFTER.days} days of our message, "
                      "the request will be closed (you can reopen it).\n" + _footer(db))
            mails = [Mail(to=req.user.email, subject=f"Reminder: we need something from you about {req.filename}", text=text)]
            counts["reminded"] += 1
        else:
            continue
        if req.user.status == "active":
            queue_mail(db, mails)
    db.commit()
    return counts

