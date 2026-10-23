"""A person's own API tokens for scripts (#518): make one (shown once), list them, revoke one. Signed-in people only:
a token can't manage tokens (portal/deps.py refuses it here)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from portal import api_tokens, approval, audit, services, settings_store
from portal.db import get_db
from portal.deps import Principal, current_principal, group_for_admin
from portal.errors import ApiError, not_found
from portal.models import ApiToken
from portal.routers.jobs import _plan

router = APIRouter(tags=["api tokens"])


class TokenOut(BaseModel):
    id: uuid.UUID
    name: str
    prefix: str
    can_submit: bool
    can_status: bool
    download: str | None
    download_kinds: list[str] | None
    processing: str
    ai_tier: str | None
    ai_kind: str | None
    sends_to: str  # "Standard · Commercial AI · Math and science": the service, AI level and kind by name
    auto_confirm: bool
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    state: str  # active | expired | revoked
    revoked_reason: str | None = None  # owner | group_admin | unused
    owner_email: str | None = None  # in a group's list of its people's tokens


class TokensOut(BaseModel):
    allowed: bool
    why_not: str | None  # what's switched off, when making tokens isn't allowed
    default_days: int
    max_days: int
    per_person: int
    download_kinds: list[str]
    tokens: list[TokenOut]


class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    processing: str = Field(services.STANDARD, max_length=64)
    ai_tier: str | None = Field(None, max_length=64)
    ai_kind: str | None = Field(None, max_length=40)
    can_status: bool = False
    download: str | None = Field(None, pattern="^(all|files)$")
    download_kinds: list[str] | None = Field(None, max_length=len(api_tokens.DOWNLOAD_KINDS))
    auto_confirm: bool = False
    days: int | None = Field(None, ge=1, le=3650)


class TokenMade(BaseModel):
    token: str  # shown once: only its hash is kept
    info: TokenOut


def _now() -> datetime:
    return datetime.now(timezone.utc)


def token_out(db: Session, t: ApiToken, with_owner: bool = False) -> TokenOut:
    level = services.get_service(db, t.ai_tier) if t.ai_tier else None
    proc = services.get_service(db, t.processing)
    parts = [proc.label if proc else t.processing]
    if t.ai_tier:
        parts.append(level.label if level else t.ai_tier)
        parts.append("Let AI decide" if t.ai_kind == services.AUTO else services.kind_label(db, t.ai_tier, t.ai_kind))
    state = "revoked" if t.revoked_at else "expired" if t.expires_at <= _now() else "active"
    return TokenOut(id=t.id, name=t.name, prefix=t.prefix, can_submit=t.can_submit, can_status=t.can_status,
                    download=t.download, download_kinds=t.download_kinds, processing=t.processing, ai_tier=t.ai_tier,
                    ai_kind=t.ai_kind, sends_to=" · ".join(parts), auto_confirm=t.auto_confirm, created_at=t.created_at,
                    expires_at=t.expires_at, last_used_at=t.last_used_at, revoked_at=t.revoked_at, state=state,
                    revoked_reason=t.revoked_reason, owner_email=t.user.email if with_owner else None)


def _active(db: Session, user_id: uuid.UUID) -> int:
    return len(db.scalars(select(ApiToken.id).where(ApiToken.user_id == user_id, ApiToken.revoked_at.is_(None),
                                                    ApiToken.expires_at > _now())).all())


@router.get("/me/api-tokens", response_model=TokensOut)
def my_tokens(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    runtime = settings_store.load(db)
    why = api_tokens.switched_on(db, p.user)
    tokens = db.scalars(select(ApiToken).where(ApiToken.user_id == p.user.id).order_by(ApiToken.created_at.desc()))
    return TokensOut(allowed=why is None, why_not=why, default_days=min(runtime.api_token_days, runtime.api_token_max_days),
                     max_days=runtime.api_token_max_days, per_person=runtime.api_tokens_per_person,
                     download_kinds=list(api_tokens.DOWNLOAD_KINDS), tokens=[token_out(db, t) for t in tokens])


@router.post("/me/api-tokens", response_model=TokenMade, status_code=201)
def make_token(body: TokenCreate, request: Request, p: Principal = Depends(current_principal),
               db: Session = Depends(get_db)):
    """A new token for a script, tied to one processing service, AI level and kind the person may use. It's in the
    answer once; only its hash is kept."""
    runtime = settings_store.load(db)
    why = api_tokens.switched_on(db, p.user)
    if why:
        raise ApiError(403, "tokens_off", why)
    approval.require_approved(p.user)
    if _active(db, p.user.id) >= runtime.api_tokens_per_person:
        raise ApiError(409, "too_many_tokens", f"You can have at most {runtime.api_tokens_per_person} tokens. "
                                               "Revoke one you no longer use first.")
    days = body.days or min(runtime.api_token_days, runtime.api_token_max_days)  # the default gives way to the longest
    if days > runtime.api_token_max_days:
        raise ApiError(422, "too_long", f"A token can last at most {runtime.api_token_max_days} days.")
    kinds = None
    if body.download == "files":
        kinds = [k for k in dict.fromkeys(body.download_kinds or []) if k in api_tokens.DOWNLOAD_KINDS]
        if not kinds:
            raise ApiError(422, "no_files", "Choose which files the token may download.")
    # The same checks as an upload: the person may use this service, AI level and kind
    chosen = services.get_service(db, body.processing)
    if chosen is None or chosen.kind != "processing":
        raise ApiError(422, "unknown_service", "That processing option doesn't exist.")
    a = services.access(db, p.user, body.processing)
    if not a.ok:
        raise ApiError(403, a.code, a.message)
    plan = _plan(db, p.user, chosen, body.processing, False, True, False, True, body.ai_tier, body.ai_kind)
    secret, prefix, digest = api_tokens.new_secret()
    token = ApiToken(user_id=p.user.id, group_id=p.user.group_id, name=body.name.strip(), prefix=prefix,
                     token_hash=digest, can_submit=True, can_status=body.can_status or body.download is not None,
                     download=body.download, download_kinds=kinds, processing=body.processing, ai_tier=plan.tier,
                     ai_kind=services.AUTO if plan.auto else plan.kind, auto_confirm=body.auto_confirm,
                     expires_at=_now() + timedelta(days=days))
    db.add(token)
    db.flush()
    audit.record(db, request, "api_token.created", actor=p.user, target_type="api_token", target_id=token.id,
                 group_id=p.user.group_id, details={"name": token.name, "prefix": prefix, "days": days,
                                                    "processing": token.processing, "ai_tier": token.ai_tier,
                                                    "ai_kind": token.ai_kind, "status": token.can_status,
                                                    "download": token.download, "files": kinds,
                                                    "auto_confirm": token.auto_confirm})
    db.commit()
    db.refresh(token)
    return TokenMade(token=secret, info=token_out(db, token))


@router.delete("/me/api-tokens/{token_id}", response_model=TokenOut)
def revoke_token(token_id: uuid.UUID, request: Request, p: Principal = Depends(current_principal),
                 db: Session = Depends(get_db)):
    """Revoke one of the person's own tokens: it stops working at once."""
    token = db.get(ApiToken, token_id)
    if token is None or token.user_id != p.user.id:
        raise not_found("Token")
    if token.revoked_at is None:
        token.revoked_at, token.revoked_by, token.revoked_reason = _now(), p.user.email, "owner"
        audit.record(db, request, "api_token.revoked", actor=p.user, target_type="api_token", target_id=token.id,
                     group_id=token.group_id, details={"name": token.name, "prefix": token.prefix, "by": "owner"})
        db.commit()
    return token_out(db, token)


# --- a group's admins: their people's tokens (never the tokens themselves) -------------------------------------------

@router.get("/groups/{group_id}/api-tokens", response_model=list[TokenOut])
def group_tokens(group_id: uuid.UUID, p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    """The tokens of the group's people, for its admins: whose, what each sends to and may do, when it expires and was
    last used. Active ones first."""
    group = group_for_admin(group_id, p, db)
    tokens = db.scalars(select(ApiToken).where(ApiToken.group_id == group.id)
                        .order_by(ApiToken.revoked_at.is_not(None), ApiToken.created_at.desc()))
    return [token_out(db, t, with_owner=True) for t in tokens]


@router.delete("/groups/{group_id}/api-tokens/{token_id}", response_model=TokenOut)
def group_revoke(group_id: uuid.UUID, token_id: uuid.UUID, request: Request, p: Principal = Depends(current_principal),
                 db: Session = Depends(get_db)):
    """A group's admin revokes one of its people's tokens: it stops working at once."""
    group = group_for_admin(group_id, p, db)
    token = db.get(ApiToken, token_id)
    if token is None or token.group_id != group.id:
        raise not_found("Token")
    if token.revoked_at is None:
        token.revoked_at, token.revoked_by, token.revoked_reason = _now(), p.user.email, "group_admin"
        audit.record(db, request, "api_token.revoked", actor=p.user, target_type="api_token", target_id=token.id,
                     group_id=group.id, details={"name": token.name, "prefix": token.prefix, "owner": token.user.email,
                                                 "by": "group_admin"})
        db.commit()
    return token_out(db, token, with_owner=True)
