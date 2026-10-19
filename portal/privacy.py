"""A person's data on request (#338): exported for a records request, or removed.

Removing someone keeps the service's records and drops who they were. Their name and email become "removed person
#abc12345" in every record that names them (the account, the audit log, the page ledger, saved versions, help
messages); their documents' files and help attachments are deleted and the file names replaced; what only served
them (sign-in sessions and codes, drafts, report subscriptions, queued email, allow-list entries, roles) is deleted.
Counts, pages, ledger totals and the audit trail of what happened stay, so reports and billing still add up.

What it can't reach: free text others wrote about them (a staff reply that names them). The docs say so.
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import datetime, timezone

from fastapi import Request
from sqlalchemy import Text, cast, delete, func, or_, select, text, update
from sqlalchemy.orm import Session

from portal import audit, removal
from portal.errors import ApiError
from portal.models import (
    AuditEvent, AuthSession, DocumentVersion, FindingMark, GroupEmail, HelpAttachment, HelpMessage, HelpRequest, Job,
    MailOutbox, OtpChallenge, QuotaLedger, ReportSend, ReportSubscription, RoleAssignment, SavedReport, TagDraft, User,
    UserPresence, UserService,
)
from portal.storage import get_storage

REMOVED_BODY = "(removed with this person's data)"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def placeholder(user: User) -> tuple[str, str]:
    """(name, email) that stand in for a removed person: unique, and plainly not a real address."""
    short = user.id.hex[:8]
    return f"removed person #{short}", f"removed-{short}@removed.invalid"


def _scrub(value, needles: list[str], name: str):
    """Their email and name, wherever they appear in a JSON value."""
    if isinstance(value, str):
        out = value
        for n in needles:
            out = re.sub(re.escape(n), name, out, flags=re.I)
        return out
    if isinstance(value, dict):
        return {k: _scrub(v, needles, name) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v, needles, name) for v in value]
    return value


def remove_person(db: Session, request: Request | None, user: User, actor: User) -> dict:
    """Remove a person (a super admin's action, on their request). Returns what was done, in numbers."""
    if user.removed_at is not None:
        raise ApiError(409, "already_removed", "This person's data has already been removed.")
    if user.id == actor.id:
        raise ApiError(409, "not_yourself", "Ask another super admin to remove your own data.")
    if user.is_super_admin and db.scalar(select(func.count()).select_from(RoleAssignment).where(
            RoleAssignment.role == "super_admin")) <= 1:
        raise ApiError(409, "last_super_admin", "The last super admin can't be removed.")
    old_email, old_name = user.email, (user.name or "").strip()
    name, email = placeholder(user)
    needles = [old_email] + ([old_name] if len(old_name) >= 3 else [])
    counts: dict[str, int] = {}

    # Their documents: files deleted (with help requests about them), names replaced
    jobs = list(db.scalars(select(Job).where(Job.user_id == user.id)))
    live = [j for j in jobs if j.files_deleted_at is None]
    counts["files_deleted"], _ = removal.remove_files(db, live) if live else (0, 0)
    for j in jobs:
        j.filename = f"document-{j.id.hex[:8]}.pdf"
        for f in j.files:
            f.name = f"{f.kind}-{f.id.hex[:8]}"
        for col in ("expedited_by", "removal_requested_by", "deleted_by"):
            if (getattr(j, col) or "").lower() == old_email:
                setattr(j, col, email)
    counts["documents"] = len(jobs)
    db.execute(update(DocumentVersion).where(func.lower(DocumentVersion.created_by_email) == old_email)
               .values(created_by_email=email, note=None))
    # Review items they signed off: the status stays, the note (their words) goes
    db.execute(update(FindingMark).where(or_(FindingMark.set_by_id == user.id,
                                             func.lower(FindingMark.set_by_email) == old_email))
               .values(set_by_email=email, note=None))

    # Their help requests: kept as records, their own words and attachments gone
    storage = get_storage()
    mine = list(db.scalars(select(HelpRequest).where(HelpRequest.user_id == user.id)))
    for req in mine:
        req.filename = None if req.filename is None else f"document-{req.id.hex[:8]}.pdf"
    msgs = list(db.scalars(select(HelpMessage).where(or_(HelpMessage.author_id == user.id,
                                                         func.lower(HelpMessage.author_email) == old_email))))
    for m in msgs:
        for a in db.scalars(select(HelpAttachment).where(HelpAttachment.message_id == m.id)):
            storage.delete_prefix(a.s3_key)
            db.delete(a)
        m.author_email, m.body = email, REMOVED_BODY
    counts["help_messages"] = len(msgs)

    # What only served them
    for model, cond in ((AuthSession, AuthSession.user_id == user.id), (UserPresence, UserPresence.user_id == user.id),
                        (TagDraft, TagDraft.user_id == user.id), (UserService, UserService.user_id == user.id),
                        (ReportSubscription, ReportSubscription.user_id == user.id),
                        (ReportSend, ReportSend.user_id == user.id), (SavedReport, SavedReport.owner_id == user.id),
                        (OtpChallenge, func.lower(OtpChallenge.email) == old_email),
                        (MailOutbox, func.lower(MailOutbox.to_addr) == old_email),
                        (GroupEmail, func.lower(GroupEmail.email) == old_email),
                        (RoleAssignment, RoleAssignment.user_id == user.id)):
        db.execute(delete(model).where(cond))
    # Emails to others that name them (a help request's notice to support): kept in the email log, without them
    for m in db.scalars(select(MailOutbox).where(or_(func.lower(MailOutbox.body).contains(old_email),
                                                     func.lower(MailOutbox.subject).contains(old_email)))):
        m.subject, m.body = _scrub(m.subject, needles, name), _scrub(m.body, needles, name)
        if m.html:
            m.html = _scrub(m.html, needles, name)
    for saved in db.scalars(select(SavedReport).where(SavedReport.recipients.contains([str(user.id)]))):
        saved.recipients = [r for r in saved.recipients if r != str(user.id)]

    # The audit log and the page ledger: the same records, without them (the only change their triggers allow, and
    # only while this runs: identity columns, nothing else)
    db.execute(text("select set_config('portal.anonymizing', 'on', true)"))
    events = list(db.scalars(select(AuditEvent).where(or_(
        AuditEvent.actor_id == user.id, func.lower(AuditEvent.actor_email) == old_email,
        AuditEvent.target_id == str(user.id), func.lower(cast(AuditEvent.details, Text)).contains(old_email)))))
    for e in events:
        if e.actor_id == user.id or (e.actor_email or "").lower() == old_email:
            e.actor_email, e.ip, e.user_agent = email, None, None
        if e.details:
            e.details = _scrub(e.details, needles, name)
    counts["audit_events"] = len(events)
    ledger = list(db.scalars(select(QuotaLedger).where(or_(QuotaLedger.actor_id == user.id,
                                                           func.lower(QuotaLedger.actor_email) == old_email))))
    for row in ledger:
        row.actor_email, row.ip = email, None
    counts["ledger_entries"] = len(ledger)
    db.flush()
    db.execute(text("select set_config('portal.anonymizing', 'off', true)"))

    user.email, user.name, user.status = email, None, "disabled"
    user.removed_at, user.removed_by = _now(), actor.email
    audit.record(db, request, "person.removed", actor=actor, target_type="user", target_id=user.id,
                 group_id=user.group_id, details={"person": name, **counts})
    return counts


def export_person(db: Session, user: User, with_files: bool) -> tuple[bytes, str, str]:
    """Everything the service holds about a person, for a records request: JSON, or a zip of the JSON and their
    documents' files that still exist. Returns (content, media type, file name)."""
    email = user.email.lower()
    jobs = list(db.scalars(select(Job).where(Job.user_id == user.id).order_by(Job.created_at)))
    reqs = list(db.scalars(select(HelpRequest).where(HelpRequest.user_id == user.id).order_by(HelpRequest.created_at)))
    events = list(db.scalars(select(AuditEvent).where(or_(
        AuditEvent.actor_id == user.id, func.lower(AuditEvent.actor_email) == email,
        AuditEvent.target_id == str(user.id))).order_by(AuditEvent.id)))
    ledger = list(db.scalars(select(QuotaLedger).where(QuotaLedger.user_id == user.id).order_by(QuotaLedger.id)))

    def iso(v):
        return v.isoformat() if isinstance(v, datetime) else v

    data = {
        "exported_at": _now().isoformat(),
        "person": {"id": str(user.id), "email": user.email, "name": user.name, "status": user.status,
                   "approval": user.approval, "group_id": str(user.group_id) if user.group_id else None,
                   "created_at": iso(user.created_at), "last_login_at": iso(getattr(user, "last_login_at", None)),
                   "roles": [r.role for r in db.scalars(select(RoleAssignment).where(RoleAssignment.user_id == user.id))]},
        "documents": [{"id": str(j.id), "filename": j.filename, "pages": j.pages, "pages_charged": j.pages_charged,
                       "status": j.status, "created_at": iso(j.created_at), "finished_at": iso(j.finished_at),
                       "files_deleted": j.files_deleted_at is not None,
                       "result": {k: (j.result_summary or {}).get(k) for k in ("compliant_before", "compliant_after")}}
                      for j in jobs],
        "help_requests": [{"id": str(r.id), "status": r.status, "created_at": iso(r.created_at),
                           "messages": [{"from": m.author_email, "at": iso(m.created_at), "body": m.body}
                                        for m in r.messages]} for r in reqs],
        "page_ledger": [{"at": iso(row.created_at), "kind": row.kind, "pages": row.pages} for row in ledger],
        "audit": [{"at": iso(e.created_at), "action": e.action, "by": e.actor_email, "outcome": e.outcome,
                   "details": e.details} for e in events],
    }
    blob = json.dumps(data, indent=2, default=str).encode()
    stamp = _now().strftime("%Y%m%d")
    if not with_files:
        return blob, "application/json", f"person-{user.id.hex[:8]}-{stamp}.json"
    buf = io.BytesIO()
    storage = get_storage()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("person.json", blob)
        for j in jobs:
            if j.files_deleted_at is not None:
                continue
            for f in j.files:
                try:
                    z.writestr(f"documents/{j.id.hex[:8]}/{f.name}", b"".join(storage.get_stream(f.s3_key)))
                except Exception:  # noqa: BLE001 - a file gone from storage is left out, not fatal
                    continue
    return buf.getvalue(), "application/zip", f"person-{user.id.hex[:8]}-{stamp}.zip"
