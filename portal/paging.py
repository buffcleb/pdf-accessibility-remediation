"""One page of a list (#514): what every paged list endpoint returns, and the search, sort and page every one takes
the same way. The web app's pager and search bar (web/src/components/lists.tsx) read it."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Generic, TypeVar

from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from portal.errors import ApiError

T = TypeVar("T")
PAGE_SIZES = (25, 50, 100, 200)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


def like(q: str) -> str:
    """A search term as a LIKE pattern: case-insensitive, its own % and _ taken literally."""
    return "%" + q.strip().lower().replace("%", r"\%").replace("_", r"\_") + "%"


def order_by(sort: str, sorts: dict, tiebreak) -> list:
    """`sort` ("email", "-last_login") as an ORDER BY, from the columns this list can sort by; the tie-break last, so
    the order is the same on every page."""
    key, desc = sort.lstrip("-"), sort.startswith("-")
    if key not in sorts:
        raise ApiError(422, "bad_sort", "Unknown sort.")
    col = sorts[key]
    return [col.desc().nulls_last() if desc else col.asc().nulls_last(), tiebreak]


def page_of(db: Session, stmt: Select, order: Sequence, page: int, page_size: int, out: Callable[[object], T]) -> Page[T]:
    """One page of `stmt` (a select of one entity, its conditions applied) in `order`, each row through `out`."""
    size = min(page_size, max(PAGE_SIZES))
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = db.scalars(stmt.order_by(*order).limit(size).offset((page - 1) * size)).all()
    return Page(items=[out(r) for r in rows], total=total, page=page, page_size=size)
