"""No AI as a choice for remediation (#659), decided top down: super admins offer it for the service (the
no_ai_enabled setting); a group's admins offer it to their people beside the AI levels ("on"), preselect it
("default"), or make it the only choice (ai_mode "fixed" with ai_level NO_AI); a person picks it per upload.

A document made with No AI goes to the engine with AI provider "none": no AI call at all, for any step. Its pages
count against its processing service only. A document with no AI level (the group has none, or none was picked and
there's no default) is made the same way, so nothing is ever sent to the engine's fallback AI.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from portal import settings_store
from portal.errors import ApiError
from portal.models import Group

NO_AI = "none"  # the AI level's key for No AI, at upload, on API tokens and as a group's fixed level
LABEL = "No AI"
SETTINGS = ("off", "on", "default")


def offered(db: Session, group: Group | None) -> bool:
    """The group's people may choose No AI: super admins offer it, and the group's admins have it on."""
    return group is not None and group.no_ai in ("on", "default") and settings_store.load(db).no_ai_enabled


def preselected(db: Session, group: Group | None) -> bool:
    """No AI is preselected at upload, ahead of the group's default AI level."""
    return offered(db, group) and group.no_ai == "default"


def require_service(db: Session) -> None:
    """Refuse No AI while super admins don't offer it."""
    if not settings_store.load(db).no_ai_enabled:
        raise ApiError(403, "no_ai_not_offered", "No AI isn't offered on this service. Choose an AI level.")


def require(db: Session, group: Group | None) -> None:
    """Refuse No AI where it isn't offered."""
    require_service(db)
    if group is None or group.no_ai not in ("on", "default"):
        raise ApiError(403, "no_ai_not_offered", "Your group doesn't offer No AI. Choose an AI level, or ask your "
                                                 "group administrator.")
