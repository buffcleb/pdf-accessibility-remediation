"""Which PDF/UA part a document is run for (#445), decided top down: super admins offer PDF/UA-2 for the service
(the pdfua2_enabled setting); a group's admins let its people choose PDF/UA-2 or Both and set what's preselected;
a person chooses per document from what their group allows. One file can't be both parts (PDF/UA-1 needs PDF 1.x and
PDF/UA-2 PDF 2.0), so Both is two files from one run.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from portal import settings_store
from portal.errors import ApiError
from portal.models import Group

PARTS = ("1", "2", "both")


def choices(db: Session, group: Group | None) -> list[str]:
    """What this group's people may choose: PDF/UA-1 only, unless the service offers PDF/UA-2 and the group allows
    the choice."""
    if group is not None and group.pdfua_choice and settings_store.load(db).pdfua2_enabled:
        return list(PARTS)
    return ["1"]


def default(db: Session, group: Group | None) -> str:
    """What's preselected: the group's default while it's allowed, else PDF/UA-1."""
    allowed = choices(db, group)
    return group.pdfua_default if group is not None and group.pdfua_default in allowed else "1"


def resolve(db: Session, group: Group | None, asked: str | None) -> str:
    """The part a document is run for: what was asked (the default when nothing was), refused when not allowed."""
    if not asked:
        return default(db, group)
    if asked not in PARTS:
        raise ApiError(422, "unknown_pdfua", "Choose PDF/UA-1, PDF/UA-2 or both.")
    if asked not in choices(db, group):
        raise ApiError(403, "pdfua_not_allowed", "Your group doesn't offer PDF/UA-2.")
    return asked
