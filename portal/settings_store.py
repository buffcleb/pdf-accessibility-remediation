"""Runtime settings that super admins change in the UI, stored in the `settings` table.

Unset keys fall back to the defaults below. Values are validated on write.
"""
from __future__ import annotations

import uuid

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from portal.models import Setting


_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _luminance(hex_color: str) -> float:
    def channel(c: int) -> float:
        v = c / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio between two #rrggbb colors."""
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def check_accent(value: str | None) -> str | None:
    """The accent colors links on white and fills buttons with white text, so it needs 4.5:1 against white
    (WCAG AA for normal text)."""
    if value in (None, ""):
        return None
    if not _HEX.match(value):
        raise ValueError("use a color like #1f4e8c")
    ratio = contrast(value, "#ffffff")
    if ratio < 4.5:
        raise ValueError(f"contrast with white is {ratio:.1f}:1; it needs at least 4.5:1 (choose a darker color)")
    return value.lower()


def check_sender_name(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    if any(c in value for c in "<>\r\n\"@"):
        raise ValueError("a sender name can't contain < > \" @ or line breaks")
    return value.strip()


def check_time_zone(value: str) -> str:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("use a time zone name like America/Denver or UTC") from None
    return value


class RuntimeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Branding (shown in the app, on the sign-in page and in emails)
    service_name: str = Field("PDF Accessibility Remediation", min_length=1, max_length=120)
    organization_name: str = Field("", max_length=200)
    logo_alt: str = Field("", max_length=300)
    logo_content_type: str | None = None  # set by the logo upload, not edited directly
    logo_version: int = 0
    contact_email: str | None = Field(None, max_length=320)
    accent_color: str | None = None  # #rrggbb; checked for contrast
    mail_from_name: str | None = Field(None, max_length=120)  # sender name on emails; default PORTAL_MAIL_FROM's

    # Messages shown to users (plain text; the SPA renders links)
    not_provisioned_message: str = Field(
        "Your email address isn't set up for this service. Contact the people who run it for your organization.",
        max_length=2000)
    account_disabled_message: str = Field(
        "Your account has been disabled. Contact your group's administrator.", max_length=2000)
    over_quota_message: str = Field(
        "You've reached your page limit. Ask your group administrator to request more pages.",
        max_length=2000)
    support_url: str | None = Field(None, max_length=500)

    # Retention
    file_retention_days: int = Field(30, ge=1, le=3650)
    record_retention_days: int = Field(2555, ge=30, le=36500)
    # A document marked done keeps its files this long, so a mistake can be undone (0: at the next sweep)
    early_removal_hold_hours: int = Field(24, ge=0, le=720)

    # Upload limits
    max_file_mb: int = Field(100, ge=1, le=2048)
    max_pages_per_document: int = Field(2000, ge=1, le=100000)
    max_files_per_upload: int = Field(20, ge=1, le=500)
    # Documents of one user processed at the same time (the rest wait their turn in the queue)
    max_concurrent_jobs_per_user: int = Field(5, ge=1, le=1000)
    # Documents one user can have waiting or in progress; uploads beyond this are refused
    max_queued_jobs_per_user: int = Field(200, ge=1, le=100000)
    # Documents in one zip export from a list of documents (#481)
    max_export_documents: int = Field(25, ge=1, le=1000)

    # Personal API tokens for scripts (#518): switched on here first, then by each group's admins. A new token lasts
    # api_token_days unless the person picks another length up to api_token_max_days; one unused for
    # api_token_idle_days stops working (0: never)
    api_tokens_enabled: bool = False
    api_token_days: int = Field(90, ge=1, le=3650)
    api_token_max_days: int = Field(365, ge=1, le=3650)
    api_tokens_per_person: int = Field(5, ge=1, le=100)

    # PDF/UA-2 for PDF 1.x documents (#445): switched on here first; then each group's admins let its people choose
    # PDF/UA-2 or Both (a PDF/UA-2 copy besides the PDF/UA-1 file) and set the group's default
    pdfua2_enabled: bool = False
    api_token_idle_days: int = Field(90, ge=0, le=3650)
    # Large documents: more pages than this (0 = off) are mixed in after every `large_doc_every` smaller ones, and
    # only `large_doc_at_once` run at a time; with nothing smaller waiting they go one after another
    large_doc_pages: int = Field(200, ge=0, le=100000)
    large_doc_every: int = Field(5, ge=1, le=1000)
    large_doc_at_once: int = Field(1, ge=1, le=100)

    # Sessions
    session_idle_hours: int = Field(8, ge=1, le=720)
    session_max_days: int = Field(7, ge=1, le=90)

    # New groups start with "new members need approval" on (each group can change it)
    new_groups_require_approval: bool = True

    # Help requests: files people and admins attach
    help_max_file_mb: int = Field(10, ge=1, le=100)
    help_max_request_mb: int = Field(50, ge=1, le=1000)
    help_reopen_days: int = Field(14, ge=0, le=365)  # how long after it's finished the person can reopen a request

    # Scheduled email reports: on or off for everyone, the time zone that decides when they go out and what
    # "last month" means, and the hour (in that time zone) they're sent
    # Notification emails, for people who haven't chosen: "each" (as they happen), "daily" (one summary) or "off"
    notify_default_documents: str = Field("each", pattern="^(each|daily|off)$")
    notify_default_help: str = Field("each", pattern="^(each|daily|off)$")
    notify_default_admin: str = Field("each", pattern="^(each|daily|off)$")
    email_reports_enabled: bool = True
    report_time_zone: str = Field("UTC", max_length=64)
    report_send_hour: int = Field(7, ge=0, le=23)

    # Kind-of-document classifier ("Let AI decide" at upload). Jev (external, through OpenRouter) for groups that
    # aren't local-only; a gateway model for local-only groups (and for everyone when Jev is off). Below the
    # threshold, General is used.
    classifier_jev: bool = False
    classifier_local_model: str = Field("", max_length=200)
    classifier_threshold: float = Field(0.6, ge=0, le=1)
    classifier_pages: int = Field(3, ge=1, le=10)
    # Alerts (#334): emailed to super admins and shown on Attention. The queue counts as stuck when documents wait and
    # none has started or finished for this long (a busy queue that's moving never is); the worker as stopped when it
    # hasn't completed a pass for this long; the engine as without workers after this long. Reminders go this often.
    alert_queue_minutes: int = Field(60, ge=5, le=1440)
    alert_worker_minutes: int = Field(15, ge=5, le=1440)
    alert_engine_minutes: int = Field(10, ge=2, le=1440)
    alert_repeat_hours: int = Field(4, ge=1, le=168)
    alert_backup_hours: int = Field(26, ge=2, le=720)  # only when the cluster runs the nightly backup
    # A pool is "running low" below this share of its size or this many pages, whichever is larger: shown on
    # Attention, and its group's admins are emailed once when it drops below (#358)
    low_pool_percent: int = Field(10, ge=0, le=100)
    low_pool_pages: int = Field(20, ge=0, le=1_000_000)
    # Security alerts (#456, portal/security_alerts.py): patterns in the audit log, emailed to super admins, shown on
    # Attention and recorded (so they reach the SIEM). Wrong codes for one address, and addresses or refused requests
    # from one IP, within `security_window_minutes`; codes asked for unknown addresses, and one person downloading
    # other people's documents, within an hour
    security_alerts: bool = True
    security_window_minutes: int = Field(15, ge=5, le=1440)
    security_failed_codes: int = Field(5, ge=2, le=1000)
    security_ip_addresses: int = Field(10, ge=2, le=10000)
    security_ip_refused: int = Field(10, ge=2, le=10000)
    security_unknown_per_hour: int = Field(20, ge=2, le=100000)
    security_downloads_per_hour: int = Field(25, ge=2, le=100000)

    # PDF/UA validation (veraPDF): its running service (fast; the engine falls back to the command line when the
    # service doesn't answer) or the command line in the engine (Java started for every check); and whether to
    # also check PDF/UA-2, shown for information (off: half as many checks)
    verapdf_mode: str = Field("service", pattern="^(service|command_line)$")
    verapdf_ua2: bool = True

    # A document that fails gets a help request of its own, so every failure is looked at (and a known limitation
    # can be closed with a note). Off: people can still ask for help with a failed document themselves
    help_on_failure: bool = True

    # "Describe with AI" in the tag manager (alt text for a drawn area, one paid AI call each): the default for
    # everyone; a group, and a person in it, can be set otherwise (portal/ai_describe.py)
    ai_describe_default: bool = False

    @field_validator("report_time_zone")
    @classmethod
    def _zone(cls, v):
        return check_time_zone(v)


class RuntimeSettingsUpdate(BaseModel):
    """Partial update: only the fields sent are changed."""
    model_config = ConfigDict(extra="forbid")

    service_name: str | None = Field(None, min_length=1, max_length=120)
    organization_name: str | None = Field(None, max_length=200)
    logo_alt: str | None = Field(None, max_length=300)
    contact_email: str | None = Field(None, max_length=320)
    accent_color: str | None = None
    mail_from_name: str | None = Field(None, max_length=120)

    @field_validator("accent_color")
    @classmethod
    def _accent(cls, v):
        return check_accent(v)

    @field_validator("mail_from_name")
    @classmethod
    def _sender(cls, v):
        return check_sender_name(v)

    @field_validator("contact_email")
    @classmethod
    def _contact(cls, v):
        if v in (None, ""):
            return None
        from email_validator import EmailNotValidError, validate_email
        try:
            return validate_email(v, check_deliverability=False).normalized
        except EmailNotValidError as e:
            raise ValueError(str(e)) from e

    not_provisioned_message: str | None = Field(None, max_length=2000)
    account_disabled_message: str | None = Field(None, max_length=2000)
    over_quota_message: str | None = Field(None, max_length=2000)
    support_url: str | None = Field(None, max_length=500)
    file_retention_days: int | None = Field(None, ge=1, le=3650)
    record_retention_days: int | None = Field(None, ge=30, le=36500)
    early_removal_hold_hours: int | None = Field(None, ge=0, le=720)
    max_file_mb: int | None = Field(None, ge=1, le=2048)
    max_pages_per_document: int | None = Field(None, ge=1, le=100000)
    max_files_per_upload: int | None = Field(None, ge=1, le=500)
    max_concurrent_jobs_per_user: int | None = Field(None, ge=1, le=1000)
    max_queued_jobs_per_user: int | None = Field(None, ge=1, le=100000)
    max_export_documents: int | None = Field(None, ge=1, le=1000)
    api_tokens_enabled: bool | None = None
    pdfua2_enabled: bool | None = None
    api_token_days: int | None = Field(None, ge=1, le=3650)
    api_token_max_days: int | None = Field(None, ge=1, le=3650)
    api_tokens_per_person: int | None = Field(None, ge=1, le=100)
    api_token_idle_days: int | None = Field(None, ge=0, le=3650)
    large_doc_pages: int | None = Field(None, ge=0, le=100000)
    large_doc_every: int | None = Field(None, ge=1, le=1000)
    large_doc_at_once: int | None = Field(None, ge=1, le=100)
    session_idle_hours: int | None = Field(None, ge=1, le=720)
    session_max_days: int | None = Field(None, ge=1, le=90)
    new_groups_require_approval: bool | None = None
    help_max_file_mb: int | None = Field(None, ge=1, le=100)
    help_max_request_mb: int | None = Field(None, ge=1, le=1000)
    help_reopen_days: int | None = Field(None, ge=0, le=365)
    notify_default_documents: str | None = Field(None, pattern="^(each|daily|off)$")
    notify_default_help: str | None = Field(None, pattern="^(each|daily|off)$")
    notify_default_admin: str | None = Field(None, pattern="^(each|daily|off)$")
    email_reports_enabled: bool | None = None
    report_time_zone: str | None = Field(None, max_length=64)
    report_send_hour: int | None = Field(None, ge=0, le=23)
    classifier_jev: bool | None = None
    classifier_local_model: str | None = Field(None, max_length=200)
    classifier_threshold: float | None = Field(None, ge=0, le=1)
    classifier_pages: int | None = Field(None, ge=1, le=10)
    alert_queue_minutes: int | None = Field(None, ge=5, le=1440)
    alert_worker_minutes: int | None = Field(None, ge=5, le=1440)
    alert_engine_minutes: int | None = Field(None, ge=2, le=1440)
    alert_repeat_hours: int | None = Field(None, ge=1, le=168)
    alert_backup_hours: int | None = Field(None, ge=2, le=720)
    low_pool_percent: int | None = Field(None, ge=0, le=100)
    low_pool_pages: int | None = Field(None, ge=0, le=1_000_000)
    security_alerts: bool | None = None
    security_window_minutes: int | None = Field(None, ge=5, le=1440)
    security_failed_codes: int | None = Field(None, ge=2, le=1000)
    security_ip_addresses: int | None = Field(None, ge=2, le=10000)
    security_ip_refused: int | None = Field(None, ge=2, le=10000)
    security_unknown_per_hour: int | None = Field(None, ge=2, le=100000)
    security_downloads_per_hour: int | None = Field(None, ge=2, le=100000)
    verapdf_mode: str | None = Field(None, pattern="^(service|command_line)$")
    verapdf_ua2: bool | None = None
    help_on_failure: bool | None = None
    ai_describe_default: bool | None = None

    @field_validator("report_time_zone")
    @classmethod
    def _zone(cls, v):
        return None if v is None else check_time_zone(v)

    @model_validator(mode="after")
    def _no_nulls(self):
        nullable = {"support_url", "contact_email", "accent_color", "mail_from_name"}
        for key in self.model_fields_set - nullable:
            if getattr(self, key) is None:
                raise ValueError(f"{key} cannot be empty")
        return self


def load(db: Session) -> RuntimeSettings:
    stored = {row.key: row.value for row in db.scalars(select(Setting))}
    known = {k: v for k, v in stored.items() if k in RuntimeSettings.model_fields}
    return RuntimeSettings(**known)


def update(db: Session, changes: RuntimeSettingsUpdate, actor_id: uuid.UUID) -> tuple[RuntimeSettings, dict]:
    """Apply changes; returns the new settings and {key: [old, new]} for the audit log."""
    current = load(db)
    sent = changes.model_dump(exclude_unset=True)
    diff = {}
    for key, value in sent.items():
        old = getattr(current, key)
        if value == old:
            continue
        row = db.get(Setting, key)
        if row is None:
            db.add(Setting(key=key, value=value, updated_by=actor_id))
        else:
            row.value = value
            row.updated_by = actor_id
        diff[key] = [old, value]
    db.flush()
    return load(db), diff


def set_values(db: Session, values: dict, actor_id: uuid.UUID) -> dict:
    """Set settings the API doesn't edit directly (the logo's type and version). Returns the diff."""
    current = load(db)
    diff = {}
    for key, value in values.items():
        old = getattr(current, key)
        if value == old:
            continue
        row = db.get(Setting, key)
        if row is None:
            db.add(Setting(key=key, value=value, updated_by=actor_id))
        else:
            row.value = value
            row.updated_by = actor_id
        diff[key] = [old, value]
    db.flush()
    return diff
