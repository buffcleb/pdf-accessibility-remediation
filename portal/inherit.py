"""A subgroup's settings follow its parent's unless it overrides them (#431).

Each setting below is kept on the subgroup's own row as it takes effect, so whatever reads a group's settings
reads a subgroup's the same way. A subgroup follows its parent for a setting unless the setting is in its
`overrides`: the parent's changes are copied down to those that follow it, and a subgroup that sets one
itself overrides it from then on, until it is told to follow the parent again.

Local-only is a data rule, so a subgroup can't loosen it: while the parent is local-only its subgroups are too,
overriding or not; when the parent stops, those that follow it stop, and those that override it keep their own.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from portal.errors import ApiError
from portal.models import Group

# A setting, and the group columns it covers (the AI one is four, set together: No AI with the levels, #659)
SETTINGS: dict[str, tuple[str, ...]] = {
    "ai": ("ai_mode", "ai_level", "ai_kind", "no_ai"),
    "local_only": ("local_only",),
    "require_approval": ("require_approval",),
    "ai_describe": ("ai_describe",),
    "show_external": ("show_external",),
    "over_quota_message": ("over_quota_message",),
}
SETTING_OF = {col: key for key, cols in SETTINGS.items() for col in cols}


def follows(group: Group, key: str) -> bool:
    """A subgroup taking this setting from its parent."""
    return group.parent_id is not None and key not in (group.overrides or [])


def inherited(group: Group) -> list[str]:
    """The settings a group takes from its parent, in SETTINGS' order (none for a group with no parent)."""
    return [k for k in SETTINGS if follows(group, k)]


def _copy(sub: Group, parent: Group, key: str) -> None:
    for col in SETTINGS[key]:
        setattr(sub, col, getattr(parent, col))


def subgroups(db: Session, group: Group) -> list[Group]:
    return list(db.scalars(select(Group).where(Group.parent_id == group.id).order_by(Group.name)))


def check_local_only(db: Session, group: Group, value: bool) -> None:
    """Refuse a subgroup turning local-only off while its parent has it on."""
    if not value and group.parent_id is not None:
        parent = db.get(Group, group.parent_id)
        if parent is not None and parent.local_only:
            raise ApiError(422, "parent_local_only", f"{parent.name} keeps its documents local, so its subgroups do "
                                                     f"too. Turn local-only off for {parent.name} first.")


def overridden(group: Group, cols: set[str]) -> None:
    """A subgroup set these columns itself: it overrides their settings from now on."""
    if group.parent_id is None:
        return
    keys = {SETTING_OF[c] for c in cols if c in SETTING_OF}
    if keys - set(group.overrides or []):
        group.overrides = sorted(set(group.overrides or []) | keys)


def follow(db: Session, group: Group, keys: list[str]) -> list[str]:
    """Make a subgroup take these settings from its parent again (their values copied now). Returns those that
    changed."""
    if group.parent_id is None:
        raise ApiError(422, "not_subgroup", "Only a subgroup takes settings from a parent.")
    unknown = [k for k in keys if k not in SETTINGS]
    if unknown:
        raise ApiError(422, "unknown_setting", f"Not a setting a subgroup can take from its parent: {', '.join(unknown)}.")
    parent = db.get(Group, group.parent_id)
    changed = [k for k in keys if not follows(group, k)]
    group.overrides = [k for k in (group.overrides or []) if k not in keys]
    for k in keys:
        _copy(group, parent, k)
    return changed


def pass_down(db: Session, group: Group, cols: set[str]) -> list[str]:
    """A group's settings changed: copy them to the subgroups that follow it, and local-only (on) to all of them.
    Returns the names of the subgroups that changed."""
    keys = {SETTING_OF[c] for c in cols if c in SETTING_OF}
    out = []
    for sub in subgroups(db, group) if keys else []:
        before = {c: getattr(sub, c) for k in keys for c in SETTINGS[k]}
        for k in keys:
            if follows(sub, k):
                _copy(sub, group, k)
        if "local_only" in keys and group.local_only:
            sub.local_only = True
        if any(getattr(sub, c) != v for c, v in before.items()):
            out.append(sub.name)
    return out


def start(sub: Group, parent: Group) -> None:
    """A new subgroup takes all its settings from its parent."""
    sub.overrides = []
    for k in SETTINGS:
        _copy(sub, parent, k)
