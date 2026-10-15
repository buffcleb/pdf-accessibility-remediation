from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from portal.config import get_portal_settings


class Base(DeclarativeBase):
    pass


@lru_cache
def get_engine() -> Engine:
    return create_engine(get_portal_settings().database_url, pool_pre_ping=True)


@lru_cache
def _sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False)


def open_session() -> Session:
    """A session for code outside a request (the body-size check runs before the request is one)."""
    return _sessionmaker()()


def get_db() -> Iterator[Session]:
    """One session per request; committed by the route, rolled back on error."""
    db = _sessionmaker()()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
