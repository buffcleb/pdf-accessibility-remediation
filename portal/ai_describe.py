"""Who may use "Describe with AI" in the tag manager: alt text for an area of a page, one AI call (paid, at the
document's AI level) each time.

It's set at three levels, each following the one above when left unset: the service default (Settings), the group
(its admins or a super admin), and the person (the same). The first one set wins."""
from __future__ import annotations

from sqlalchemy.orm import Session

from portal import settings_store
from portal.models import User


def allowed(db: Session, user: User) -> bool:
    if user.ai_describe is not None:
        return user.ai_describe
    if user.group is not None and user.group.ai_describe is not None:
        return user.group.ai_describe
    return bool(settings_store.load(db).ai_describe_default)


def source(db: Session, user: User) -> str:
    """Where the answer comes from: "person", "group" or "service"."""
    if user.ai_describe is not None:
        return "person"
    if user.group is not None and user.group.ai_describe is not None:
        return "group"
    return "service"
