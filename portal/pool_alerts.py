"""A group's pool running low: its admins are emailed once (#358).

A pool counts as low below `low_pool_percent` of its size or `low_pool_pages` pages, whichever is larger (Settings;
Attention uses the same). The worker checks about once a minute: when a pool drops below, the group's admins (or the
super admins, if it has none) get one email; topping it up above re-arms it. An empty pool is low too. A subgroup's
limit on its parent's pool (#431) counts the same way, and its admins and its parent's are told.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from portal import outbox, services, settings_store
from portal.approval import _admins_of
from portal.mail import Mail
from portal.models import AlertState, Group

PREFIX = "pool:"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_low(left: int, pool: int, rs) -> bool:
    return left < max(rs.low_pool_pages, rs.low_pool_percent / 100 * pool)


def _key(group_id, service_key: str) -> str:
    return PREFIX + hashlib.sha1(f"{group_id}:{service_key}".encode()).hexdigest()[:32]


def check(db: Session, now: datetime | None = None) -> list[str]:
    """Email the admins of groups whose pool just ran low. Returns the keys emailed about."""
    rs = settings_store.load(db)
    now = now or _now()
    known = {r.key: r for r in db.scalars(select(AlertState).where(AlertState.key.like(PREFIX + "%")))}
    low: dict[str, tuple] = {}
    for g in db.scalars(select(Group).where(Group.status == "active")):
        for gs in g.services:
            s = services.get_service(db, gs.service_key)
            if s is None or not s.enabled or not gs.enabled or not s.metered:
                continue
            if g.parent_id is not None and gs.cap_pages is None:
                continue  # a subgroup with no limit of its own: its parent's pool is told about
            size, left = services.pool_left(g, s.key) or (0, 0)
            if size > 0 and is_low(left, size, rs):
                low[_key(g.id, s.key)] = (g, s, left, size)
    sent = []
    footer = f"\n{rs.service_name}" + (f", {rs.organization_name}" if rs.organization_name else "") + "\n"
    for key, (g, s, left, pool) in low.items():
        if key in known:
            continue
        detail = f"{g.name}: {left:,} of {pool:,} {s.label} pages left."
        what = "has run out" if left <= 0 else "is running low"
        outbox.queue(db, [Mail(to=to, subject=f"{g.name}: {s.label} pages {'used up' if left <= 0 else 'running low'}",
                               text=f"Your group's {'limit' if g.parent_id else 'pool'} of {s.label} pages {what}: "
                                    f"{left:,} of {pool:,} left. People in "
                                    f"the group can't send documents with it once it's empty.\n\nTo add pages, ask "
                                    f"your service's administrators (the group's Services and pages page shows how)."
                                    f"\n{footer}") for to in _admins_of(db, g)], rs.mail_from_name, "alert")
        db.add(AlertState(key=key, detail=detail, started_at=now, notified_at=now))
        sent.append(key)
    for key, row in known.items():
        if key not in low:
            db.delete(row)  # topped up: the next drop is told again
    return sent
