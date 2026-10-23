"""Rolling the database back (#454, docs/operations/rollback.md): `python -m portal.cli downgrade REVISION` takes it
back to an earlier migration, as the owner, and refuses a revision that doesn't exist or isn't earlier. The latest
migration's downgrade is run for real and then applied again."""
from __future__ import annotations

from sqlalchemy import text

from portal import cli
from portal.config import get_portal_settings
from portal.db import _sessionmaker, get_engine
from tests.portal.conftest import APP_PASSWORD, APP_ROLE_SQL, TEST_DB


def _as_owner(monkeypatch):
    """Migrations run as the tables' owner (the app's own account can't change them)."""
    monkeypatch.setenv("PORTAL_DATABASE_URL", TEST_DB)
    get_portal_settings.cache_clear()


def _restore_app_settings():
    for cached in (get_portal_settings, get_engine, _sessionmaker):
        cached.cache_clear()


def _current(engine) -> str:
    with engine.connect() as c:
        return c.execute(text("select version_num from alembic_version")).scalar()


def test_a_revision_that_isnt_earlier_is_refused(engine, monkeypatch, capsys):
    _as_owner(monkeypatch)
    try:
        head = _current(engine)
        assert cli.main(["downgrade", "9999"]) == 2
        assert "no migration" in capsys.readouterr().err
        assert cli.main(["downgrade", head]) == 2
        assert "isn't before it" in capsys.readouterr().err
        assert _current(engine) == head
    finally:
        _restore_app_settings()


def test_the_latest_migration_goes_back_and_forward_again(engine, monkeypatch, capsys):
    _as_owner(monkeypatch)
    head = _current(engine)
    previous = f"{int(head) - 1:04d}"
    try:
        assert cli.main(["downgrade", previous]) == 0
        assert f"from {head} to {previous}" in capsys.readouterr().out
        assert _current(engine) == previous
    finally:
        cli.migrate()
        with engine.begin() as c:  # the app's account gets the rows of what was made again, as deploy.sh does
            c.execute(text(APP_ROLE_SQL.read_text().replace("__APP_PASSWORD__", APP_PASSWORD)))
        _restore_app_settings()
    assert _current(engine) == head
