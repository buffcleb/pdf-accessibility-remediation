"""#337: on the cluster the portal runs as its own database account (deploy/k8s/app-role.sql): it reads and writes
rows, but can't change a table, drop the triggers that keep the audit log and page ledger append-only, or touch the
migrations' bookkeeping. This runs the same file the deploy runs against the test database."""
from __future__ import annotations

import secrets
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from portal import audit
from tests.portal.conftest import TEST_DB

SQL = Path(__file__).resolve().parents[2] / "deploy" / "k8s" / "app-role.sql"
PASSWORD = secrets.token_urlsafe(16)  # a throwaway for the test database's portal_app


@pytest.fixture
def app_engine(db, engine):
    with engine.begin() as c:
        c.execute(text(SQL.read_text().replace("__APP_PASSWORD__", PASSWORD)))
        c.execute(text("select 1"))
    app = create_engine(make_url(TEST_DB).set(username="portal_app", password=PASSWORD))
    yield app
    app.dispose()
    with engine.begin() as c:
        c.execute(text("drop owned by portal_app; drop role portal_app"))


def _refused(app, sql: str) -> str:
    with app.connect() as c, pytest.raises(DBAPIError) as err:
        c.execute(text(sql))
        c.commit()
    return str(err.value.orig)


def test_the_app_account_works_with_rows_only(app_engine):
    # Ordinary work: read, and write (an audit entry, whose id comes from a sequence)
    with Session(app_engine) as s:
        s.execute(text("select count(*) from users")).scalar()
        audit.record(s, None, "test.app_account", actor_email="system")
        s.commit()
        assert s.execute(text("select count(*) from audit_events where action = 'test.app_account'")).scalar() == 1
    # ...but not the tables themselves, the append-only triggers, or the migrations' bookkeeping
    assert "owner" in _refused(app_engine, "drop trigger audit_events_no_update on audit_events")
    assert "owner" in _refused(app_engine, "alter table users add column note text")
    assert "permission denied" in _refused(app_engine, "create table scratch (x int)")
    assert "permission denied" in _refused(app_engine, "update alembic_version set version_num = version_num")
    # The triggers still hold for it, as for everyone
    assert "append-only" in _refused(app_engine, "update audit_events set action = 'changed'")


def test_running_it_again_is_harmless(app_engine, engine):
    with engine.begin() as c:
        c.execute(text(SQL.read_text().replace("__APP_PASSWORD__", PASSWORD)))
    with app_engine.connect() as c:
        assert c.execute(text("select count(*) from jobs")).scalar() == 0


def test_tables_later_migrations_make_are_the_apps_too(app_engine, engine):
    with engine.begin() as c:  # as the owner, as a migration would
        c.execute(text("create table future_table (id serial primary key, note text)"))
    try:
        with app_engine.begin() as c:
            c.execute(text("insert into future_table (note) values ('made after the grants')"))
            assert c.execute(text("select count(*) from future_table")).scalar() == 1
    finally:
        with engine.begin() as c:
            c.execute(text("drop table future_table"))
