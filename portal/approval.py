"""Member approval: in a group that requires it, new members can sign in straight away but can't submit
anything (uploads, tag edits, reviews) until one of the group's admins approves them.

Approval is kept apart from the account status (active/disabled), so sign-in and sessions work as before.
Admins (group admins, super admins) are always treated as approved.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from portal import audit, outbox, settings_store
from portal.config import get_portal_settings
from portal.errors import ApiError
from portal.mail import Mail
from portal.models import Group, RoleAssignment, User

log = logging.getLogger(__name__)

PENDING_MESSAGE = ("Your account is waiting for approval by your group's administrator. You can submit documents "
                   "once it's approved; you'll get an email.")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def starts_pending(group: Group | None, bootstrap: bool) -> bool:
    return group is not None and group.require_approval and not bootstrap


def require_approved(user: User) -> None:
    """Refuse work from someone who isn't approved, with a reason the app can show."""
    if user.approved:
        return
    if user.approval == "declined":
        reason = f" The reason given: {user.decline_reason}" if user.decline_reason else ""
        raise ApiError(403, "approval_declined",
                       f"Your request to use this service was declined by your group's administrator.{reason}")
    raise ApiError(403, "pending_approval", PENDING_MESSAGE)


def pending_count(db: Session, group_ids: list | None = None) -> int:
    """People waiting in these groups (all groups when None)."""
    stmt = select(func.count()).select_from(User).where(User.approval == "pending", User.status == "active")
    if group_ids is not None:
        if not group_ids:
            return 0
        stmt = stmt.where(User.group_id.in_(group_ids))
    return db.scalar(stmt) or 0


def mark_approved_as_admin(user: User, actor_email: str) -> None:
    """Admins don't wait: making someone an admin approves them."""
    if user.approval != "approved":
        user.approval, user.approval_at, user.approval_by, user.decline_reason = "approved", _now(), actor_email, None


def approve(db: Session, request: Request, user: User, actor: User) -> None:
    if user.approval == "approved":
        raise ApiError(409, "already_approved", f"{user.email} is already approved.")
    old = user.approval
    user.approval, user.approval_at, user.approval_by, user.decline_reason = "approved", _now(), actor.email, None
    audit.record(db, request, "user.approved", actor=actor, target_type="user", target_id=user.id,
                 group_id=user.group_id, details={"email": user.email, "was": old})


def decline(db: Session, request: Request, user: User, actor: User, reason: str | None) -> None:
    if user.approval != "pending":
        raise ApiError(409, "not_pending", f"{user.email} isn't waiting for approval.")
    user.approval, user.approval_at, user.approval_by = "declined", _now(), actor.email
    user.decline_reason = reason or None
    audit.record(db, request, "user.declined", actor=actor, target_type="user", target_id=user.id,
                 group_id=user.group_id, details={"email": user.email, "reason": user.decline_reason})


# --- emails ----------------------------------------------------------------------------------------

def _admins_of(db: Session, group: Group) -> list[str]:
    """The group's active admins; if it has none, the super admins, so a request is never lost."""
    def active(rows):
        return sorted({r.user.email for r in rows if r.user.status == "active"})
    admins = active(db.scalars(select(RoleAssignment).where(RoleAssignment.role == "group_admin",
                                                            RoleAssignment.group_id == group.id)))
    return admins or active(db.scalars(select(RoleAssignment).where(RoleAssignment.role == "super_admin")))


def _signature(db: Session) -> str:
    rs = settings_store.load(db)
    return f"\n{rs.service_name}" + (f", {rs.organization_name}" if rs.organization_name else "") + "\n"




def notify_waiting(db: Session, user: User) -> None:
    """Tell the group's admins someone new is waiting (one email per request)."""
    group = user.group
    if group is None:
        return
    rs = settings_store.load(db)
    link = f"{get_portal_settings().app_url.rstrip('/')}/admin/groups/{group.id}/people"
    mails = [Mail(to=to, subject=f"{user.email} is waiting for approval in {group.name}",
                  text=(f"{user.email} signed in to {rs.service_name} for the first time and is waiting for approval "
                        f"to use it in {group.name}.\n\nApprove or decline them here:\n{link}\n"
                        f"\nUntil then they can sign in but can't submit documents.\n" + _signature(db)))
             for to in _admins_of(db, group)]
    outbox.queue(db, mails, rs.mail_from_name, "approval")


def notify_decision(db: Session, user: User) -> None:
    rs = settings_store.load(db)
    url = get_portal_settings().app_url
    if user.approval == "approved":
        mail = Mail(to=user.email, subject=f"You can now use {rs.service_name}",
                    text=(f"Your account has been approved. You can now submit documents:\n{url}\n" + _signature(db)))
    else:
        reason = f"\nThe reason given:\n{user.decline_reason}\n" if user.decline_reason else ""
        contact = f"\nQuestions? Contact {rs.contact_email}.\n" if rs.contact_email else ""
        mail = Mail(to=user.email, subject=f"Your request to use {rs.service_name}",
                    text=(f"Your group's administrator declined your request to use {rs.service_name}.\n{reason}"
                          f"{contact}" + _signature(db)))
    outbox.queue(db, [mail], rs.mail_from_name, "account")  # about their own account: always sent
