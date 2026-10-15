"""Database tables: identity, groups, roles, sessions, audit, settings, services and quotas, jobs.

IDs are random UUIDs so they can't be guessed or enumerated. All timestamps are UTC.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from portal.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _id() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), default=utcnow)


class Group(Base):
    """A logical group of users (an organization, campus, department, team...). Its page pools are per service
    (GroupService)."""
    __tablename__ = "groups"

    id: Mapped[uuid.UUID] = _id()
    name: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    over_quota_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Documents of a local-only group never go to services that send them to an external provider
    local_only: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    # New members wait for a group admin's approval before they can submit anything
    require_approval: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    # How the group's documents get their AI: "choose" (people pick a level and kind) or "fixed" (always ai_level
    # and ai_kind; people aren't asked). Set by the group's admins.
    ai_mode: Mapped[str] = mapped_column(String(8), nullable=False, default="choose", server_default="choose")
    ai_level: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ai_kind: Mapped[str | None] = mapped_column(String(40), nullable=True)  # None: General
    # Show people which options send documents to an external provider (off: the badge is hidden)
    show_external: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    # "Describe with AI" for the group's people: True/False, or None to follow the service default
    ai_describe: Mapped[bool | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = _created()

    domains: Mapped[list[GroupDomain]] = relationship(back_populates="group", cascade="all, delete-orphan",
                                                      order_by="GroupDomain.domain")
    emails: Mapped[list[GroupEmail]] = relationship(back_populates="group", cascade="all, delete-orphan",
                                                    order_by="GroupEmail.email")
    services: Mapped[list[GroupService]] = relationship(back_populates="group", cascade="all, delete-orphan",
                                                        lazy="selectin")

    __table_args__ = (
        CheckConstraint("status in ('active','disabled')", name="group_status"),
    )

    def service(self, key: str) -> GroupService | None:
        return next((s for s in self.services if s.service_key == key), None)


class GroupEmail(Base):
    """One person allowed into a group by their address, without their whole domain (a gmail.com user, say).
    Unique across all groups; takes precedence over a domain match."""
    __tablename__ = "group_emails"

    email: Mapped[str] = mapped_column(String(320), primary_key=True)
    group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    added_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    created_at: Mapped[datetime] = _created()

    group: Mapped[Group] = relationship(back_populates="emails")

    __table_args__ = (CheckConstraint("email = lower(email)", name="group_email_lower"),)


class GroupDomain(Base):
    """An email domain whose users are provisioned into a group. Unique across all groups."""
    __tablename__ = "group_domains"

    domain: Mapped[str] = mapped_column(String(253), primary_key=True)
    group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at: Mapped[datetime] = _created()

    group: Mapped[Group] = relationship(back_populates="domains")

    __table_args__ = (CheckConstraint("domain = lower(domain)", name="domain_lower"),)


class User(Base):
    """One account per email address. The group is fixed when the account is created."""
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _id()
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Super admins created by bootstrap may have no group.
    group_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("groups.id", ondelete="RESTRICT"), nullable=True, index=True)
    auth_method: Mapped[str] = mapped_column(String(16), nullable=False, default="email")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    # Separate from status: people waiting for approval (or declined) can sign in, but not submit anything
    approval: Mapped[str] = mapped_column(String(16), nullable=False, default="approved", server_default="approved")
    approval_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approval_by: Mapped[str | None] = mapped_column(String(320), nullable=True)  # the approver's email
    decline_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created()
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # How often they want each kind of notification email: {"documents"|"help"|"admin": "each"|"daily"|"off"}.
    # Missing: the service default (Settings → Email).
    notify_prefs: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    # "Describe with AI" for this person: True/False, or None to follow their group (and it the service default)
    ai_describe: Mapped[bool | None] = mapped_column(nullable=True)

    group: Mapped[Group | None] = relationship()
    roles: Mapped[list[RoleAssignment]] = relationship(back_populates="user", cascade="all, delete-orphan",
                                                       lazy="selectin")
    services: Mapped[list[UserService]] = relationship(back_populates="user", cascade="all, delete-orphan",
                                                       lazy="selectin")

    __table_args__ = (
        CheckConstraint("email = lower(email)", name="email_lower"),
        CheckConstraint("status in ('active','disabled')", name="user_status"),
        CheckConstraint("approval in ('pending','approved','declined')", name="user_approval"),
        Index("ix_users_group_pending", "group_id", postgresql_where=text("approval = 'pending'")),
        CheckConstraint("auth_method in ('email','saml')", name="user_auth_method"),
    )

    @property
    def is_super_admin(self) -> bool:
        return any(r.role == "super_admin" for r in self.roles)

    def admin_group_ids(self) -> set[uuid.UUID]:
        return {r.group_id for r in self.roles if r.role == "group_admin" and r.group_id}

    @property
    def approved(self) -> bool:
        """Can submit work. Admins always can, whatever their approval says."""
        return self.approval == "approved" or self.is_super_admin or bool(self.admin_group_ids())

    def service(self, key: str) -> UserService | None:
        return next((s for s in self.services if s.service_key == key), None)


# --- services and their quotas -------------------------------------------------------------------

class Service(Base):
    """A way of processing documents ("standard", "adobe") or an AI tier ("ai:local"), with its global
    switch, data-handling flags, cost guards and global page pool. Groups and users opt in per service."""
    __tablename__ = "services"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # processing | ai_tier
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=False)  # the global opt-in
    external: Mapped[bool] = mapped_column(nullable=False, default=False)  # documents go to an external provider
    notice_enabled: Mapped[bool] = mapped_column(nullable=False, default=False)
    notice_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    metered: Mapped[bool] = mapped_column(nullable=False, default=True)  # False: usage counted, never limited
    # Global pool; None = no global limit (group pools and user caps still apply)
    pool_pages: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    used_pages: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reserved_pages: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    daily_cap_pages: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # pages sent per UTC day
    max_pages_per_job: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Documents processed at once with this service; None = no limit of its own. For what can't grow with the
    # engine: a local AI's GPUs, an outside provider's rate limit. The rest wait in the queue.
    max_at_once: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confirm_before_send: Mapped[bool] = mapped_column(nullable=False, default=False)
    gateway_model: Mapped[str | None] = mapped_column(String(200), nullable=True)  # AI tiers: LiteLLM model
    # AI tiers: the review (judge) model, from a different family than gateway_model; None = the engine default
    gateway_judge_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # AI tiers: whether people may ask for every figure to be reviewed (more AI calls) instead of a sample
    allow_full_review: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    # AI tiers: whether the AI reads specialist notation (math, chemistry, medical, engineering) the
    # layout rules can't read
    allow_notation: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    # AI tiers: whether the AI corrects OCR words Tesseract was unsure of on scanned pages
    allow_ocr_ai: Mapped[bool] = mapped_column(nullable=False, default=True, server_default="true")
    # Processing services: the engine route that does the work (standard | adobe)
    engine_route: Mapped[str] = mapped_column(String(32), nullable=False, default="standard", server_default="standard")
    # AI levels: audits (check only) with this level "free" (use AI, charge nothing), "charge" (use AI, charge this
    # level's pages) or "off" (no AI in audits)
    audit_ai: Mapped[str] = mapped_column(String(8), nullable=False, default="free", server_default="free")
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = _created()

    __table_args__ = (
        CheckConstraint("kind in ('processing','ai_tier')", name="service_kind"),
        CheckConstraint("engine_route in ('standard','adobe')", name="service_engine_route"),
        CheckConstraint("used_pages >= 0 and reserved_pages >= 0 and (pool_pages is null or pool_pages >= 0)",
                        name="service_pages_nonneg"),
    )


class AiKind(Base):
    """A kind of document within an AI level (Math and science, Language arts, Forms…), with the gateway
    models that suit it. General isn't a row: it's the level's own models (Service.gateway_model)."""
    __tablename__ = "ai_kinds"

    service_key: Mapped[str] = mapped_column(ForeignKey("services.key", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    gateway_model: Mapped[str] = mapped_column(String(200), nullable=False)
    gateway_judge_model: Mapped[str | None] = mapped_column(String(200), nullable=True)  # None: the level's
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = _created()

    __table_args__ = (CheckConstraint("key <> 'general'", name="ai_kind_not_general"),)


class GroupService(Base):
    """A group's opt-in to a service and its page pool for it."""
    __tablename__ = "group_services"

    group_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True)
    service_key: Mapped[str] = mapped_column(ForeignKey("services.key", ondelete="CASCADE"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=False)
    all_users: Mapped[bool] = mapped_column(nullable=False, default=False)  # granted to every member
    pool_pages: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    used_pages: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reserved_pages: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    default_user_cap: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # None = unlimited
    is_default: Mapped[bool] = mapped_column(nullable=False, default=False)  # preselected at upload

    group: Mapped[Group] = relationship(back_populates="services")
    service: Mapped[Service] = relationship(lazy="selectin")

    __table_args__ = (
        CheckConstraint("pool_pages >= 0 and used_pages >= 0 and reserved_pages >= 0", name="group_service_nonneg"),
    )


class UserService(Base):
    """A user's grant for a service, their cap and usage."""
    __tablename__ = "user_services"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    service_key: Mapped[str] = mapped_column(ForeignKey("services.key", ondelete="CASCADE"), primary_key=True)
    granted: Mapped[bool] = mapped_column(nullable=False, default=False)
    cap_pages: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # None = the group default
    used_pages: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reserved_pages: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    user: Mapped[User] = relationship(back_populates="services")

    __table_args__ = (
        CheckConstraint("used_pages >= 0 and reserved_pages >= 0", name="user_service_nonneg"),
    )


class RoleAssignment(Base):
    __tablename__ = "role_assignments"

    id: Mapped[uuid.UUID] = _id()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    group_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), nullable=True)
    created_at: Mapped[datetime] = _created()

    user: Mapped[User] = relationship(back_populates="roles")

    __table_args__ = (
        CheckConstraint(
            "(role = 'super_admin' and group_id is null) or (role = 'group_admin' and group_id is not null)",
            name="role_scope"),
        UniqueConstraint("user_id", "role", "group_id", name="role_unique"),
        # NULLs are distinct in a unique constraint, so super_admin needs its own index
        Index("role_one_super_admin", "user_id", unique=True, postgresql_where="role = 'super_admin'"),
    )


class OtpChallenge(Base):
    """An emailed one-time code. Only an HMAC of the code is stored."""
    __tablename__ = "otp_challenges"

    id: Mapped[uuid.UUID] = _id()
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    code_hmac: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    created_at: Mapped[datetime] = _created()


class AuthSession(Base):
    """A signed-in session. The refresh token rotates on every use; only its hash is stored.

    previous_hash keeps the last rotated-out token so that reuse of a stolen, already-used token is
    detected and revokes the session.
    """
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = _id()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    refresh_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    previous_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = _created()
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(400), nullable=True)
    ip: Mapped[str | None] = mapped_column(INET, nullable=True)

    user: Mapped[User] = relationship()


class AuditEvent(Base):
    """Append-only record of sign-ins, admin actions, file access and exports."""
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = _created()
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    actor_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, default="success")
    target_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The group the event belongs to, so group admins can see their own group's events
    group_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(400), nullable=True)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (Index("audit_created_idx", "created_at"), Index("audit_group_created_idx", "group_id", "created_at"))


class Setting(Base):
    """Runtime settings changed by super admins (messages, limits, retention)."""
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[object] = mapped_column(JSONB, nullable=False)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow,
                                                 onupdate=utcnow)


JOB_STATUSES = ("queued", "submitted", "processing", "completed", "failed", "canceled")
ACTIVE_JOB_STATUSES = ("held", "queued", "submitted", "processing")


class Job(Base):
    """One PDF submitted for remediation. Pages are reserved at upload and committed or released at the end."""
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = _id()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    # The group charged; kept even if the user later moves
    group_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("groups.id", ondelete="SET NULL"), nullable=True, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    pages: Mapped[int] = mapped_column(Integer, nullable=False)
    pages_charged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 0 for report-only
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    report_only: Mapped[bool] = mapped_column(nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", index=True)
    engine_job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ai_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ai_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # How it's processed (a processing service key) and the services whose pages it was charged to
    processing: Mapped[str] = mapped_column(String(64), nullable=False, default="standard", server_default="standard")
    ai_tier: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The kind of document within the AI level ("math", …); None: General (the level's own models)
    ai_kind: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Why it was processed with the level's General models instead of its kind's (the kind's model wasn't
    # available, or failed); None: as chosen
    ai_kind_fallback: Mapped[str | None] = mapped_column(String(32), nullable=True)
    charged_services: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    external_services: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    cost: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    full_review: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    # An audit with no AI (its level says so): the engine is told to use none
    no_ai: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    # "Let AI decide": the kind is picked by the classifier before the document is sent; what it said is kept
    # ({"kind", "confidence", "used", "by", "reason"}; no model names)
    ai_kind_auto: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    ai_kind_pick: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    progress: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    result_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    submit_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # "Process now" (super admins): a waiting large document goes ahead of the large-document rules
    expedited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expedited_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    created_at: Mapped[datetime] = _created()
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    files_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # "I'm done with it": the files go once the hold (early_removal_hold_hours) ends, not at the end of retention
    removal_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    removal_requested_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    # Deleted by a super admin: gone from every document list, still counted in reports
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by: Mapped[str | None] = mapped_column(String(320), nullable=True)

    user: Mapped[User] = relationship()
    files: Mapped[list[JobFile]] = relationship(back_populates="job", cascade="all, delete-orphan",
                                                order_by="JobFile.created_at", lazy="selectin")

    __table_args__ = (
        # held: waiting for the user to confirm sending it to a service that asks for confirmation
        CheckConstraint("status in ('held','queued','submitted','processing','completed','failed','canceled')",
                        name="job_status"),
        CheckConstraint("pages >= 0 and pages_charged >= 0", name="job_pages_nonneg"),
        Index("jobs_group_created_idx", "group_id", "created_at"),
        Index("jobs_created_idx", "created_at"),
        Index("jobs_user_created_idx", "user_id", "created_at"),
        Index("jobs_removal_requested_idx", "removal_requested_at",
              postgresql_where=text("removal_requested_at IS NOT NULL AND files_deleted_at IS NULL")),
    )


class JobFile(Base):
    __tablename__ = "job_files"

    id: Mapped[uuid.UUID] = _id()
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    # input | primary | visual_fallback | report_md | report_json | verapdf_before | verapdf_after | adobe_report | enhanced
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    s3_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = _created()

    job: Mapped[Job] = relationship(back_populates="files")


class QuotaLedger(Base):
    """Append-only record of every change to a group's pool or a user's usage.

    topup/adjust change the pool (super admin). reserve/commit/release follow jobs.
    Balances after each entry are stored so any figure in a report can be traced.
    """
    __tablename__ = "quota_ledger"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = _created()
    # None for entries on a service's global pool
    group_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), nullable=True, index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    service_key: Mapped[str] = mapped_column(String(64), nullable=False, default="standard", server_default="standard")
    scope: Mapped[str] = mapped_column(String(8), nullable=False, default="group", server_default="group")
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    pages: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ticket_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ticket_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    override: Mapped[bool] = mapped_column(nullable=False, default=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    actor_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    pool_after: Mapped[int] = mapped_column(BigInteger, nullable=False)
    used_after: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reserved_after: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        # correct: a super admin's correction of one person's usage (pages given back or charged)
        CheckConstraint("kind in ('topup','adjust','reserve','commit','release','correct')", name="ledger_kind"),
        CheckConstraint("kind <> 'correct' or (user_id is not null and comment is not null)", name="ledger_correct"),
        # A top-up needs a ticket, or an override with a comment
        CheckConstraint("kind <> 'topup' or ticket_ref is not null or (override and comment is not null)",
                        name="ledger_topup_ticket"),
        CheckConstraint("not override or comment is not null", name="ledger_override_comment"),
        CheckConstraint("(scope = 'global' and group_id is null) or (scope = 'group' and group_id is not null)",
                        name="ledger_scope"),
        Index("ledger_service_created_idx", "service_key", "created_at"),
        Index("ledger_group_created_idx", "group_id", "created_at"),
        Index("ledger_created_idx", "created_at"),
    )


VERSION_KINDS = ("original", "primary", "visual_fallback", "enhanced", "reviewed")
VERSION_STATUSES = ("queued", "submitted", "processing", "ready", "failed")


class DocumentVersion(Base):
    """One viewable version of a job's document: the original, each engine output, and every
    human-reviewed version saved from the tag manager. Engine outputs are never modified; edits
    always produce a new reviewed version."""
    __tablename__ = "document_versions"

    id: Mapped[uuid.UUID] = _id()
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    based_on_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document_versions.id", ondelete="SET NULL"),
                                                          nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_by_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ready", index=True)
    engine_job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    edits: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf_file_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("job_files.id", ondelete="SET NULL"), nullable=True)
    # A corrected file uploaded by support: what the engine checks (with no edits)
    source_file_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("job_files.id", ondelete="SET NULL"),
                                                             nullable=True)
    tagmap_file_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("job_files.id", ondelete="SET NULL"), nullable=True)
    verapdf_file_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("job_files.id", ondelete="SET NULL"),
                                                              nullable=True)
    summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created()
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Saved "into the same reviewed version": this one takes the place (and label) of the one it replaces once
    # it's ready. The replaced one is then kept for a day, out of sight, so the save can be undone
    replaces_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document_versions.id", ondelete="SET NULL"),
                                                          nullable=True)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # AI tag review: requested when saving ("sampled"), or later for a saved version. It re-checks the
    # same file and replaces the version's review findings; the file itself never changes.
    review: Mapped[str | None] = mapped_column(String(16), nullable=True)
    review_status: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    review_engine_job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    review_requested_by: Mapped[str | None] = mapped_column(String(320), nullable=True)
    review_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    job: Mapped[Job] = relationship()

    __table_args__ = (
        UniqueConstraint("job_id", "number", name="version_number_unique"),
        CheckConstraint("review is null or review in ('sampled','full')", name="version_review"),
        CheckConstraint("review_status is null or review_status in ('queued','running','done','failed')",
                        name="version_review_status"),
        CheckConstraint("kind in ('original','primary','visual_fallback','enhanced','reviewed','corrected')",
                        name="version_kind"),
        CheckConstraint("status in ('queued','submitted','processing','ready','failed')", name="version_status"),
    )


# --- help requests -------------------------------------------------------------------------------

class TagDraft(Base):
    """Someone's unsaved tag edits on a document, kept between sessions: reviewing a long document takes days, and
    edits held only in a browser tab are lost when it closes. One per person per document; it goes when they save
    a version from it or discard it."""
    __tablename__ = "tag_drafts"

    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    user_email: Mapped[str] = mapped_column(String(320), nullable=False)
    based_on: Mapped[int] = mapped_column(Integer, nullable=False)  # the version number the edits apply to
    edits: Mapped[list] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


HELP_KINDS = ("alt_text", "reading_order", "tables", "headings", "math", "visual", "other", "failed")
HELP_STATUSES = ("open", "in_progress", "waiting", "resolved", "closed")


class HelpRequest(Base):
    """Someone asking for help with one of their remediated documents. Super admins work through these; the
    person, their group's admins and super admins talk it over in the request's messages."""
    __tablename__ = "help_requests"

    id: Mapped[uuid.UUID] = _id()
    # SET NULL: a request saved past retention outlives the document's record
    job_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    group_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("groups.id", ondelete="SET NULL"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open", index=True)
    version_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tag: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # {"i", "role", "text"} from the tag manager
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    assignee_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    saved: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()
    # Who wrote last on each side, for "replied" in the queue and unread dots
    person_wrote_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    staff_wrote_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    person_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # How it ended: what was done (the person sees it), the version made, pages given back
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_version_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pages_refunded: Mapped[int | None] = mapped_column(Integer, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Reminders sent while waiting on the person (after 3 and 7 days; closed at 10)
    reminders_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Email batching: several messages within a few minutes go out as one email per side
    person_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    person_notify_pending: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")
    staff_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    staff_notify_pending: Mapped[bool] = mapped_column(nullable=False, default=False, server_default="false")

    user: Mapped[User] = relationship(foreign_keys=[user_id])
    group: Mapped[Group | None] = relationship()
    messages: Mapped[list[HelpMessage]] = relationship(back_populates="request", cascade="all, delete-orphan",
                                                        order_by="HelpMessage.created_at")

    __table_args__ = (
        CheckConstraint(f"kind in {HELP_KINDS}", name="help_kind"),
        CheckConstraint(f"status in {HELP_STATUSES}", name="help_status"),
        # One request per document still being worked on
        Index("help_created_idx", "created_at"),
        Index("help_one_active_per_job", "job_id", unique=True,
              postgresql_where=text("status in ('open','in_progress','waiting')")),
    )

    @property
    def active(self) -> bool:
        return self.status in ("open", "in_progress", "waiting")

    @property
    def needs_staff(self) -> bool:
        """New, or the person (or their group admin) wrote since a super admin last did."""
        if self.status == "open":
            return True
        return self.status == "in_progress" and self.person_wrote_at is not None and (
            self.staff_wrote_at is None or self.person_wrote_at > self.staff_wrote_at)

    @property
    def unread_for_person(self) -> bool:
        last = max((t for t in (self.staff_wrote_at, self.status_changed_at) if t), default=None)
        return last is not None and (self.person_seen_at is None or last > self.person_seen_at)


class HelpMessage(Base):
    """A message on a help request, an internal note (super admins only), or a recorded event (status change)."""
    __tablename__ = "help_messages"

    id: Mapped[uuid.UUID] = _id()
    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("help_requests.id", ondelete="CASCADE"), nullable=False,
                                                  index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="message")
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    author_email: Mapped[str] = mapped_column(String(320), nullable=False)
    author_role: Mapped[str] = mapped_column(String(16), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = _created()

    request: Mapped[HelpRequest] = relationship(back_populates="messages")
    attachments: Mapped[list[HelpAttachment]] = relationship(back_populates="message", cascade="all, delete-orphan",
                                                             order_by="HelpAttachment.created_at", lazy="selectin")

    __table_args__ = (
        CheckConstraint("kind in ('message','note','event')", name="help_message_kind"),
        CheckConstraint("author_role in ('person','group_admin','super_admin','system')", name="help_author_role"),
    )


class HelpAttachment(Base):
    __tablename__ = "help_attachments"

    id: Mapped[uuid.UUID] = _id()
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("help_messages.id", ondelete="CASCADE"), nullable=False,
                                                  index=True)
    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("help_requests.id", ondelete="CASCADE"), nullable=False,
                                                  index=True)
    s3_key: Mapped[str] = mapped_column(String(512), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = _created()

    message: Mapped[HelpMessage] = relationship(back_populates="attachments")


class QueueRemoval(Base):
    """A failed item a super admin removed from a queue's list (kind: documents, edits, reviews or mail). The item
    itself is untouched: the document, its history and its audit trail stay; it just stops being listed as failed."""
    __tablename__ = "queue_removals"

    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    removed_by: Mapped[str] = mapped_column(String(320), nullable=False)
    removed_at: Mapped[datetime] = _created()


class QueuePause(Base):
    """A paused queue: one row while it's paused. Keys: "service:<key>" (documents for that processing service or
    AI level), "edits" (saved edits), "reviews" (AI reviews of saved versions), "mail" (outgoing email other than
    sign-in codes). Work already started finishes; nothing new is started until it's resumed."""
    __tablename__ = "queue_pauses"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    paused_by: Mapped[str] = mapped_column(String(320), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    paused_at: Mapped[datetime] = _created()


# held: waiting for the person's daily summary; combined: went out in one; seen: not sent, they saw it in the portal
MAIL_STATUSES = ("queued", "sent", "failed", "canceled", "held", "combined", "seen")


class MailOutbox(Base):
    """Outgoing email, sent by the worker so it can be seen, paused and retried, and the log of every email sent.
    Sign-in codes and test emails are sent at once, then recorded here as sent or failed, without their body."""
    __tablename__ = "mail_outbox"

    id: Mapped[uuid.UUID] = _id()
    kind: Mapped[str] = mapped_column(String(40), nullable=False)  # job, help, approval…
    to_addr: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    html: Mapped[str | None] = mapped_column(Text, nullable=True)  # an HTML version, when there's more than text
    # What it's about ("job:<id>", "help:<id>"): not sent if the person saw it in the portal (see notifications)
    about: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # Files sent with it: [{"name", "type", "data" (base64)}] (scheduled report views' CSV)
    attachments: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    from_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_try_at: Mapped[datetime] = _created()
    created_at: Mapped[datetime] = _created()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    backend: Mapped[str | None] = mapped_column(String(16), nullable=True)  # smtp, ses, cloudflare… (when tried)
    # Our Message-ID header, or the provider's ID for the message: to find it in the provider's or recipient's logs
    message_id: Mapped[str | None] = mapped_column(String(300), nullable=True)

    __table_args__ = (
        CheckConstraint("status in ('queued','sent','failed','canceled','held','combined','seen')",
                        name="mail_outbox_status"),
        Index("mail_outbox_status_next_idx", "status", "next_try_at"),
        Index("mail_outbox_created_idx", "created_at"),
        Index("mail_outbox_to_idx", "to_addr"),
    )


REPORT_KINDS = ("my_month", "group_summary", "service_summary")
REPORT_FREQUENCIES = ("monthly", "weekly", "daily", "off")


class ReportSubscription(Base):
    """Someone's choice for one email report. No row: the default (monthly, for those it applies to)."""
    __tablename__ = "report_subscriptions"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    frequency: Mapped[str] = mapped_column(String(16), nullable=False)
    updated_at: Mapped[datetime] = _created()

    __table_args__ = (
        CheckConstraint("kind in ('my_month','group_summary','service_summary')", name="report_sub_kind"),
        CheckConstraint("frequency in ('monthly','weekly','daily','off')", name="report_sub_frequency"),
    )


class SavedReport(Base):
    """A report view an admin saved to be emailed on a schedule: the report, its filters, search, sort and
    columns, and its group. Each period it's run for the period just ended and sent to its recipients who still
    have the same access."""
    __tablename__ = "saved_reports"

    id: Mapped[uuid.UUID] = _id()
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    report: Mapped[str] = mapped_column(String(40), nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)  # q, sort, cols and the report's filters
    group_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), nullable=True)
    frequency: Mapped[str] = mapped_column(String(16), nullable=False, default="monthly")
    recipients: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)  # user ids (strings)
    attach_csv: Mapped[bool] = mapped_column(nullable=False, default=False)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()

    owner: Mapped[User] = relationship()

    __table_args__ = (
        CheckConstraint("frequency in ('monthly','weekly','daily')", name="saved_report_frequency"),
    )


class ReportSend(Base):
    """One scheduled report email for one person and period: recorded so a period is never sent twice."""
    __tablename__ = "report_sends"

    id: Mapped[uuid.UUID] = _id()
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    frequency: Mapped[str] = mapped_column(String(16), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    kinds: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # queued | skipped (nothing to report)
    outbox_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("mail_outbox.id", ondelete="SET NULL"), nullable=True)
    # A scheduled report view (None: the built-in reports)
    saved_report_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("saved_reports.id", ondelete="SET NULL"),
                                                              nullable=True)
    created_at: Mapped[datetime] = _created()

    __table_args__ = (
        Index("report_send_once", "user_id", "frequency", "period_start", unique=True,
              postgresql_where=text("saved_report_id is null")),
        Index("report_view_send_once", "saved_report_id", "user_id", "period_start", unique=True,
              postgresql_where=text("saved_report_id is not null")),
        Index("report_sends_created_idx", "created_at"),
    )


class UserPresence(Base):
    """What someone has open in the portal: the app reports it every half minute while the page is in view
    ("job:<id>", "help:<id>", or "jobs" for their documents list). Kept a day."""
    __tablename__ = "user_presence"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    item: Mapped[str] = mapped_column(String(80), primary_key=True)
    seen_at: Mapped[datetime] = _created()
