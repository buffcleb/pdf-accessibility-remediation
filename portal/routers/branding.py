"""Branding (public, so the sign-in page can show it) and its administration, plus mail status and a test email
for super admins."""
from __future__ import annotations

import io

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from portal import audit, outbox, settings_store
from portal.security import botcheck
from portal.config import get_portal_settings
from portal.db import get_db
from portal.deps import Principal, require_super_admin
from portal.errors import ApiError, not_found
from portal.mail import MailError, get_mailer, sender, test_mail
from portal.storage import get_storage

router = APIRouter(tags=["branding"])

LOGO_KEY = "branding/logo"
LOGO_MAX_BYTES = 512 * 1024
# Raster formats only: an SVG served from the app's own origin could carry script
LOGO_TYPES = {b"\x89PNG\r\n\x1a\n": "image/png", b"\xff\xd8\xff": "image/jpeg", b"RIFF": "image/webp"}


class BrandingOut(BaseModel):
    service_name: str
    organization_name: str
    logo_url: str | None
    logo_alt: str
    accent_color: str | None
    support_url: str | None
    contact_email: str | None
    bot_check_site_key: str | None = None  # Cloudflare Turnstile's site key, when the sign-in bot check is on


def branding_out(rs: settings_store.RuntimeSettings) -> BrandingOut:
    return BrandingOut(
        service_name=rs.service_name, organization_name=rs.organization_name,
        logo_url=f"/api/v1/branding/logo?v={rs.logo_version}" if rs.logo_content_type else None,
        logo_alt=rs.logo_alt, accent_color=rs.accent_color, support_url=rs.support_url,
        contact_email=rs.contact_email,
        bot_check_site_key=get_portal_settings().turnstile_site_key or None if botcheck.enabled() else None)


@router.get("/branding", response_model=BrandingOut)
def get_branding(db: Session = Depends(get_db)):
    """Public: the service's name, organization, logo and colors, for every page including sign-in."""
    return branding_out(settings_store.load(db))


@router.get("/branding/logo")
def get_logo(db: Session = Depends(get_db)):
    rs = settings_store.load(db)
    if not rs.logo_content_type:
        raise not_found("Logo")
    try:
        data = b"".join(get_storage().get_stream(LOGO_KEY))
    except Exception:  # noqa: BLE001 - a missing object is just "no logo"
        raise not_found("Logo")
    return Response(data, media_type=rs.logo_content_type, headers={
        "Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'"})


def _sniff(data: bytes) -> str | None:
    for magic, ctype in LOGO_TYPES.items():
        if data.startswith(magic) and (ctype != "image/webp" or data[8:12] == b"WEBP"):
            return ctype
    return None


@router.put("/admin/branding/logo", response_model=BrandingOut)
def put_logo(request: Request, file: UploadFile = File(...), p: Principal = Depends(require_super_admin),
             db: Session = Depends(get_db)):
    data = file.file.read(LOGO_MAX_BYTES + 1)
    if len(data) > LOGO_MAX_BYTES:
        raise ApiError(413, "logo_too_large", "The logo must be 512 KB or smaller.")
    ctype = _sniff(data)
    if ctype is None:
        raise ApiError(415, "logo_type", "Use a PNG, JPEG or WebP image for the logo.")
    get_storage().put_file(LOGO_KEY, io.BytesIO(data), ctype)
    rs = settings_store.load(db)
    diff = settings_store.set_values(db, {"logo_content_type": ctype, "logo_version": rs.logo_version + 1}, p.user.id)
    audit.record(db, request, "settings.logo_updated", actor=p.user, target_type="settings",
                 details={"bytes": len(data), "type": ctype, **diff})
    db.commit()
    return branding_out(settings_store.load(db))


@router.delete("/admin/branding/logo", response_model=BrandingOut)
def delete_logo(request: Request, p: Principal = Depends(require_super_admin), db: Session = Depends(get_db)):
    get_storage().delete_prefix(LOGO_KEY)
    rs = settings_store.load(db)
    diff = settings_store.set_values(db, {"logo_content_type": None, "logo_version": rs.logo_version + 1}, p.user.id)
    audit.record(db, request, "settings.logo_removed", actor=p.user, target_type="settings", details=diff)
    db.commit()
    return branding_out(settings_store.load(db))


# --- mail ------------------------------------------------------------------------------------------------

class MailStatus(BaseModel):
    backend: str
    from_name: str
    from_address: str
    reply_to: str | None


class MailTestIn(BaseModel):
    to: str | None = Field(None, max_length=320)  # default: the signed-in super admin


class MailTestOut(BaseModel):
    sent: bool
    to: str
    backend: str
    error: str | None = None


@router.get("/admin/mail", response_model=MailStatus)
def mail_status(_: Principal = Depends(require_super_admin), db: Session = Depends(get_db)):
    s = get_portal_settings()
    name, address = sender(settings_store.load(db).mail_from_name)
    return MailStatus(backend=s.mail_backend, from_name=name, from_address=address, reply_to=s.mail_reply_to or None)


@router.post("/admin/mail/test", response_model=MailTestOut)
def mail_test(body: MailTestIn, request: Request, p: Principal = Depends(require_super_admin),
              db: Session = Depends(get_db)):
    """Send a test email (to yourself by default) and report what happened."""
    from email_validator import EmailNotValidError, validate_email

    to = body.to or p.user.email
    try:
        to = validate_email(to, check_deliverability=False).normalized
    except EmailNotValidError:
        raise ApiError(422, "invalid_email", "That isn't a valid email address.")
    rs = settings_store.load(db)
    backend = get_portal_settings().mail_backend
    error = None
    mail = test_mail(to, rs.service_name)
    try:
        get_mailer().deliver(mail, from_name=rs.mail_from_name, attempts=1)
    except MailError as e:
        error = str(e)
    outbox.record(db, mail, "test", error=error)
    audit.record(db, request, "mail.test_sent", actor=p.user, target_type="settings",
                 outcome="success" if error is None else "failure",
                 details={"to": to, "backend": backend, **({"error": error[:300]} if error else {})})
    db.commit()
    return MailTestOut(sent=error is None, to=to, backend=backend, error=error)
