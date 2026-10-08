"""Sign-in with an emailed one-time code, session refresh and sign-out."""
from __future__ import annotations

import logging

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from portal import approval, audit, settings_store
from portal.config import get_portal_settings
from portal.db import get_db
from portal.deps import Principal, current_principal
from portal.errors import ApiError, forbidden, too_many, unauthorized
from portal.identity import eligibility, normalize_email, provision
from portal.mail import MailError, get_mailer, sign_in_code_mail
from portal.models import AuthSession, OtpChallenge, User
from portal.schemas import MeOut, me_out
from portal.security.ratelimit import RateLimiter, get_rate_limiter
from portal.security.tokens import (
    constant_time_equal, issue_access_token, keyed_hash, new_otp_code, new_refresh_token, otp_hash,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# A refresh token presented again within this many seconds of being rotated is treated as a
# concurrent request from another tab, not as theft.
REFRESH_GRACE_SECONDS = 20


class CodeRequest(BaseModel):
    email: str = Field(max_length=320)


class CodeVerify(BaseModel):
    email: str = Field(max_length=320)
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: MeOut


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _check_origin(request: Request) -> None:
    """Cookie-authenticated endpoints only accept same-site browser requests from allowed origins."""
    origin = request.headers.get("origin")
    if origin is not None and origin.rstrip("/") not in get_portal_settings().cors_origin_list:
        raise forbidden("Request origin not allowed.")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise forbidden("Cross-site request refused.")


def _set_refresh_cookie(response: Response, token: str, max_age: int) -> None:
    s = get_portal_settings()
    response.set_cookie(
        s.refresh_cookie_name, token, max_age=max_age, path="/api/v1/auth", httponly=True,
        secure=s.cookie_secure, samesite="strict", domain=s.cookie_domain or None)


def _clear_refresh_cookie(response: Response) -> None:
    s = get_portal_settings()
    response.delete_cookie(s.refresh_cookie_name, path="/api/v1/auth", domain=s.cookie_domain or None,
                           secure=s.cookie_secure, httponly=True, samesite="strict")


def _blocked_message(db: Session, user: User) -> str | None:
    if user.status != "active":
        return settings_store.load(db).account_disabled_message
    if not user.is_super_admin and (user.group is None or user.group.status != "active"):
        return settings_store.load(db).account_disabled_message
    return None


def issue_code(db: Session, email: str, ip: str | None = None) -> str:
    """A new sign-in code for this address (only the newest works). Only its hash is stored. Caller commits."""
    s = get_portal_settings()
    db.execute(update(OtpChallenge).where(OtpChallenge.email == email, OtpChallenge.consumed_at.is_(None))
               .values(consumed_at=_now()))
    code = new_otp_code()
    challenge = OtpChallenge(email=email, code_hmac="", expires_at=_now() + timedelta(minutes=s.otp_minutes), ip=ip)
    db.add(challenge)
    db.flush()
    challenge.code_hmac = otp_hash(email, challenge.id, code)
    return code


@router.post("/code", status_code=202)
def request_code(body: CodeRequest, request: Request, background: BackgroundTasks,
                 db: Session = Depends(get_db), limiter: RateLimiter = Depends(get_rate_limiter)):
    s = get_portal_settings()
    email = normalize_email(body.email)
    ip = audit.client_ip(request) or "unknown"
    if not limiter.hit("otp-ip", ip, s.rate_otp_per_ip, s.rate_window_seconds) or \
            not limiter.hit("otp-email", email, s.rate_otp_per_email, s.rate_window_seconds):
        audit.record(db, request, "login.code_requested", actor_email=email, outcome="rate_limited")
        db.commit()
        raise too_many()

    elig = eligibility(db, email)
    runtime = settings_store.load(db)
    if not elig.allowed:
        audit.record(db, request, "login.code_requested", actor_email=email, outcome="not_provisioned")
        db.commit()
        raise ApiError(403, "not_provisioned", runtime.not_provisioned_message)
    if elig.user is not None and (msg := _blocked_message(db, elig.user)):
        audit.record(db, request, "login.code_requested", actor=elig.user, outcome="disabled",
                     group_id=elig.user.group_id)
        db.commit()
        raise ApiError(403, "account_disabled", msg)

    code = issue_code(db, email, audit.client_ip(request))
    audit.record(db, request, "login.code_requested", actor=elig.user, actor_email=email,
                 group_id=elig.user.group_id if elig.user else (elig.group.id if elig.group else None))
    db.commit()
    # Sent before answering, so a mail problem is reported to the person instead of a code that never arrives
    try:
        get_mailer().deliver(sign_in_code_mail(email, code, s.otp_minutes, runtime.service_name),
                             from_name=runtime.mail_from_name)
    except MailError as e:
        log.error("sign-in code to %s not sent: %s", email, e)
        audit.record(db, request, "login.code_not_sent", actor=elig.user, actor_email=email, outcome="failure",
                     details={"error": str(e)[:300]})
        db.commit()
        raise ApiError(503, "mail_failed", "We couldn't send your sign-in code just now. Try again in a few minutes; "
                                           "if it keeps happening, contact the people who run this service.")
    return {"status": "code_sent", "expires_in": s.otp_minutes * 60}


@router.post("/code/verify", response_model=TokenOut)
def verify_code(body: CodeVerify, request: Request, response: Response,
                db: Session = Depends(get_db), limiter: RateLimiter = Depends(get_rate_limiter)):
    s = get_portal_settings()
    email = normalize_email(body.email)
    ip = audit.client_ip(request) or "unknown"
    if not limiter.hit("verify-ip", ip, s.rate_verify_per_ip, s.rate_window_seconds):
        raise too_many()

    invalid = ApiError(400, "invalid_code", "That code is wrong or has expired. Request a new one.")
    challenge = db.scalar(
        select(OtpChallenge).where(OtpChallenge.email == email, OtpChallenge.consumed_at.is_(None))
        .order_by(OtpChallenge.created_at.desc()).limit(1).with_for_update())
    if challenge is None or challenge.expires_at <= _now() or challenge.attempts >= s.otp_max_attempts:
        audit.record(db, request, "login", actor_email=email, outcome="invalid_code")
        db.commit()
        raise invalid
    challenge.attempts += 1
    if not constant_time_equal(challenge.code_hmac, otp_hash(email, challenge.id, body.code)):
        if challenge.attempts >= s.otp_max_attempts:
            challenge.consumed_at = _now()
        audit.record(db, request, "login", actor_email=email, outcome="invalid_code",
                     details={"attempt": challenge.attempts})
        db.commit()
        raise invalid
    challenge.consumed_at = _now()

    # Eligibility is checked again: the domain or group may have changed since the code was sent.
    elig = eligibility(db, email)
    runtime = settings_store.load(db)
    if not elig.allowed:
        audit.record(db, request, "login", actor_email=email, outcome="not_provisioned")
        db.commit()
        raise ApiError(403, "not_provisioned", runtime.not_provisioned_message)
    user = elig.user
    created = user is None
    if created:
        user = provision(db, email, "email", elig)
        audit.record(db, request, "user.provisioned", actor=user, target_type="user", target_id=user.id,
                     group_id=user.group_id, details={"method": "email", "super_admin": elig.bootstrap})
        if user.approval == "pending":
            audit.record(db, request, "user.approval_requested", actor=user, target_type="user", target_id=user.id,
                         group_id=user.group_id, details={"email": user.email})
            approval.notify_waiting(db, user)
    if msg := _blocked_message(db, user):
        audit.record(db, request, "login", actor=user, outcome="disabled", group_id=user.group_id)
        db.commit()
        raise ApiError(403, "account_disabled", msg)

    token = new_refresh_token()
    session = AuthSession(
        user_id=user.id, refresh_hash=keyed_hash(token, "refresh"),
        expires_at=_now() + timedelta(days=runtime.session_max_days),
        user_agent=audit.user_agent(request), ip=audit.client_ip(request))
    db.add(session)
    user.last_login_at = _now()
    db.flush()
    audit.record(db, request, "login", actor=user, group_id=user.group_id, details={"method": "email"})
    db.commit()

    access, ttl = issue_access_token(user.id, session.id)
    _set_refresh_cookie(response, token, runtime.session_max_days * 86400)
    return TokenOut(access_token=access, expires_in=ttl, user=me_out(db, user))


@router.post("/refresh", response_model=TokenOut)
def refresh(request: Request, response: Response, db: Session = Depends(get_db)):
    _check_origin(request)
    s = get_portal_settings()
    token = request.cookies.get(s.refresh_cookie_name)
    if not token:
        raise unauthorized()
    digest = keyed_hash(token, "refresh")
    now = _now()
    session = db.scalar(select(AuthSession).where(AuthSession.refresh_hash == digest).with_for_update())
    if session is None:
        reused = db.scalar(select(AuthSession).where(AuthSession.previous_hash == digest).with_for_update())
        if reused is not None and reused.revoked_at is None:
            if (now - reused.last_used_at).total_seconds() > REFRESH_GRACE_SECONDS:
                # An old refresh token was replayed: assume it was stolen and end the session.
                reused.revoked_at = now
                reused.revoked_reason = "refresh_reuse"
                audit.record(db, request, "session.revoked", actor=reused.user, outcome="refresh_reuse",
                             target_type="session", target_id=reused.id, group_id=reused.user.group_id)
                db.commit()
        _clear_refresh_cookie(response)
        raise unauthorized()

    runtime = settings_store.load(db)
    idle = (now - session.last_used_at) > timedelta(hours=runtime.session_idle_hours)
    if session.revoked_at is not None or session.expires_at <= now or idle:
        if session.revoked_at is None:
            session.revoked_at = now
            session.revoked_reason = "idle" if idle else "expired"
            db.commit()
        _clear_refresh_cookie(response)
        raise unauthorized("Your session has ended. Sign in again.")
    user = session.user
    if _blocked_message(db, user):
        session.revoked_at = now
        session.revoked_reason = "disabled"
        db.commit()
        _clear_refresh_cookie(response)
        raise unauthorized("Your account has been disabled.")

    new_token = new_refresh_token()
    session.previous_hash = session.refresh_hash
    session.refresh_hash = keyed_hash(new_token, "refresh")
    session.last_used_at = now
    db.commit()
    access, ttl = issue_access_token(user.id, session.id)
    remaining = int((session.expires_at - now).total_seconds())
    _set_refresh_cookie(response, new_token, remaining)
    return TokenOut(access_token=access, expires_in=ttl, user=me_out(db, user))


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    _check_origin(request)
    s = get_portal_settings()
    token = request.cookies.get(s.refresh_cookie_name)
    if token:
        session = db.scalar(select(AuthSession).where(AuthSession.refresh_hash == keyed_hash(token, "refresh")))
        if session is not None and session.revoked_at is None:
            session.revoked_at = _now()
            session.revoked_reason = "logout"
            audit.record(db, request, "logout", actor=session.user, group_id=session.user.group_id)
            db.commit()
    _clear_refresh_cookie(response)
    response.status_code = 204
    return response


@router.post("/logout-all", status_code=204)
def logout_all(request: Request, response: Response, p: Principal = Depends(current_principal),
               db: Session = Depends(get_db)):
    count = db.execute(
        update(AuthSession).where(AuthSession.user_id == p.user.id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=_now(), revoked_reason="logout_all")).rowcount
    audit.record(db, request, "logout_all", actor=p.user, group_id=p.user.group_id, details={"sessions": count})
    db.commit()
    _clear_refresh_cookie(response)
    response.status_code = 204
    return response
