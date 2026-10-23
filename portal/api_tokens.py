"""Personal API tokens for scripts (#518).

A token acts as its owner for their own documents only: it may submit (always), and if they chose, check status and
download. It can't reach anything else, Administration and Settings included, whoever owns it (portal/deps.py
refuses it everywhere but the document routes that ask for it). Each token is tied to one processing service, AI
level and kind, chosen when it's made.

Tokens are switched on from the top down: for the service (super admins, the api_tokens_enabled setting), then for a
group (its admins), then made by its people. Switching off at any level stops the tokens already made, at once:
every use checks every level, as every use of a session checks the session.

The token is random, starts with "pdfr_" (so secret scanners can spot a leaked one), and is shown once; only its
HMAC under the server secret is kept, as with sign-in codes and refresh tokens.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from portal import outbox, rls, settings_store
from portal.config import get_portal_settings
from portal.errors import ApiError, unauthorized
from portal.mail import Mail
from portal.models import ApiToken, Group, User
from portal.security.tokens import keyed_hash

PREFIX = "pdfr_"
SCOPES = ("submit", "status", "download")
# What a "chosen files" download may include: the document's files people come for (STAFF_ONLY_FILES never)
DOWNLOAD_KINDS = ("primary", "enhanced", "visual_fallback", "report_pdf", "input", "music_files", "music_score",
                  "math_list", "math_files", "math_nemeth", "math_ueb")
_PURPOSE = "api_token"
_TOUCH_EVERY = timedelta(minutes=5)  # last_used_at isn't rewritten on every request


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_token(bearer: str) -> bool:
    return bearer.startswith(PREFIX)


def new_secret() -> tuple[str, str, str]:
    """A new token: (the token to show once, its prefix for telling tokens apart, its hash to keep)."""
    token = PREFIX + secrets.token_urlsafe(32)
    return token, token[:len(PREFIX) + 6], keyed_hash(token, _PURPOSE)


def switched_on(db: Session, user: User) -> str | None:
    """Why this person can't use tokens now, or None: the service's switch, then their group's."""
    if not settings_store.load(db).api_tokens_enabled:
        return "API tokens are switched off for this service."
    group = db.get(Group, user.group_id) if user.group_id else None
    if group is None or group.status != "active":
        return "API tokens need a group."
    if not group.api_tokens_enabled:
        return "Your group's administrators haven't switched API tokens on."
    return None


def authenticate(db: Session, request: Request, bearer: str) -> tuple[User, ApiToken]:
    """The token's owner and the token, checked at every level; refused with 401 otherwise. Sets row-level security to
    the owner's own group (never the super-admin bypass: a token sees its owner's documents, however senior they
    are)."""
    rls.bypass(db, "api token lookup")  # the token row is found by its hash before anyone is known
    token = db.scalar(select(ApiToken).where(ApiToken.token_hash == keyed_hash(bearer, _PURPOSE)))
    now = _now()
    if token is None or token.revoked_at is not None:
        raise unauthorized("This API token isn't valid. Make a new one in Settings.")
    if token.expires_at <= now:
        raise unauthorized("This API token has expired. Make a new one in Settings.")
    idle = settings_store.load(db).api_token_idle_days
    if idle and (token.last_used_at or token.created_at) <= now - timedelta(days=idle):
        raise unauthorized(f"This API token wasn't used for {idle} days and has stopped working. Make a new one in Settings.")
    user = token.user
    if user.status != "active" or user.removed_at is not None:
        raise unauthorized("Your account has been disabled.")
    if user.approval != "approved":
        raise unauthorized("Your account isn't approved to submit documents.")
    if user.group_id != token.group_id:
        raise unauthorized("This API token was made in another group. Make a new one in Settings.")
    why = switched_on(db, user)
    if why:
        raise unauthorized(why)
    if token.last_used_at is None or token.last_used_at < now - _TOUCH_EVERY:
        token.last_used_at = now
    rls.as_token(db, user)
    return user, token


def require(token: ApiToken, scope: str) -> None:
    """403 unless the token may do this."""
    allowed = {"submit": token.can_submit, "status": token.can_status or token.download is not None,
               "download": token.download is not None}[scope]
    if not allowed:
        raise ApiError(403, "token_scope", f"This API token can't {'check status' if scope == 'status' else scope}. "
                                           "Make one that can in Settings.")


def may_download(token: ApiToken, kind: str) -> bool:
    """Whether this token may download a file of this kind: with "all", every file the owner may; with "files",
    the kinds chosen."""
    if token.download == "all":
        return True
    return token.download == "files" and kind in (token.download_kinds or [])


WARN_BEFORE = timedelta(days=7)


def sweep(db: Session, now: datetime | None = None) -> dict:
    """Worker: tell owners a token expires within a week (once), and stop and tell them about one unused for the
    idle limit (api_token_idle_days). Emails always go: they're about access, not a notification to choose."""
    now = now or _now()
    rs = settings_store.load(db)
    link = get_portal_settings().app_url.rstrip("/") + "/settings#tokens"
    footer = f"\n{rs.service_name}" + (f", {rs.organization_name}" if rs.organization_name else "") + "\n"
    counts = {"tokens_warned": 0, "tokens_stopped_unused": 0}
    live = [ApiToken.revoked_at.is_(None), ApiToken.expires_at > now]
    for t in db.scalars(select(ApiToken).where(*live, ApiToken.expiry_warned_at.is_(None),
                                               ApiToken.expires_at <= now + WARN_BEFORE).limit(500)):
        t.expiry_warned_at = now
        counts["tokens_warned"] += 1
        if t.user.status == "active":
            outbox.queue(db, [Mail(to=t.user.email, subject=f"Your API token \"{t.name}\" expires on {t.expires_at:%Y-%m-%d}",
                                   text=f"Your API token \"{t.name}\" ({t.prefix}…) stops working on "
                                        f"{t.expires_at:%Y-%m-%d}. Scripts using it will be refused from then.\n\n"
                                        f"Make a new one and put it in the script before then:\n{link}\n" + footer)],
                         rs.mail_from_name, "api_token")
    if rs.api_token_idle_days:
        idle_since = now - timedelta(days=rs.api_token_idle_days)
        for t in db.scalars(select(ApiToken).where(*live, func.coalesce(ApiToken.last_used_at, ApiToken.created_at)
                                                   <= idle_since).limit(500)):
            t.revoked_at, t.revoked_by, t.revoked_reason, t.idle_notified_at = now, "system", "unused", now
            counts["tokens_stopped_unused"] += 1
            if t.user.status == "active":
                outbox.queue(db, [Mail(to=t.user.email, subject=f"Your API token \"{t.name}\" has stopped working",
                                       text=f"Your API token \"{t.name}\" ({t.prefix}…) wasn't used for "
                                            f"{rs.api_token_idle_days} days, so it has stopped working.\n\n"
                                            f"If a script still needs one, make a new token:\n{link}\n" + footer)],
                             rs.mail_from_name, "api_token")
    db.commit()
    return counts
