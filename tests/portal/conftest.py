"""Portal API tests run against a real PostgreSQL (constraints, locks and triggers matter).

Start one with:
    docker run -d --rm --name portal-test-db -e POSTGRES_USER=portal -e POSTGRES_PASSWORD=test \
        -e POSTGRES_DB=portal -p 55432:5432 postgres:16-alpine
or point PORTAL_TEST_DATABASE_URL at another empty database. Tests are skipped if none is reachable, unless
PORTAL_TEST_DATABASE_REQUIRED is set (as in CI), when they fail.

As on the cluster, the migrations run as the database's owner and the app as its own account, portal_app
(deploy/k8s/app-role.sql), which row-level security binds (#350). The tests' own `db` session is the owner's: it sets
things up and looks at the results, whatever the policies say.
"""
from __future__ import annotations

import os
import re
import secrets
from pathlib import Path

import pytest

TEST_DB = os.environ.get("PORTAL_TEST_DATABASE_URL", "postgresql+psycopg://portal:test@localhost:55432/portal")
ORIGIN = "http://app.test"
APP_PASSWORD = secrets.token_urlsafe(16)  # a throwaway for the test database's portal_app
APP_ROLE_SQL = Path(__file__).resolve().parents[2] / "deploy" / "k8s" / "app-role.sql"

from portal.security.tokens import generate_private_key_pem  # noqa: E402  (no settings needed)

os.environ.update({
    # Uploads are page-counted in-process in tests (a child process per upload doubles the suite's time); the
    # isolation itself is tested in test_jobs.py with it turned on
    "PORTAL_PDFCHECK_ISOLATED": "false",
    "PORTAL_DATABASE_URL": TEST_DB,
    "PORTAL_JWT_PRIVATE_KEYS": generate_private_key_pem(),
    "PORTAL_SECRET_KEY": "test-secret-" + "x" * 40,
    "PORTAL_MAIL_BACKEND": "memory",
    "PORTAL_COOKIE_SECURE": "false",
    "PORTAL_CORS_ORIGINS": ORIGIN,
    "PORTAL_BOOTSTRAP_SUPER_ADMINS": "root@admin.test",
    "PORTAL_RATE_OTP_PER_EMAIL": "5",
    "PORTAL_STORAGE_BACKEND": "memory",
    "PORTAL_APP_URL": "http://app.test",
})

import email_validator  # noqa: E402
import fakeredis  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from portal import rls  # noqa: E402
from portal.cli import migrate  # noqa: E402
from portal.config import get_portal_settings  # noqa: E402
from portal.db import _sessionmaker, get_engine, system_session  # noqa: E402
from portal.mail import get_mailer  # noqa: E402
from portal.main import create_app  # noqa: E402
from portal.models import Group, GroupDomain, RoleAssignment, User  # noqa: E402
from portal.security.ratelimit import RateLimiter, get_rate_limiter  # noqa: E402

email_validator.TEST_ENVIRONMENT = True  # allow the reserved .test domain

TABLES = "worker_beats, alert_states, user_presence, report_sends, saved_reports, report_subscriptions, mail_outbox, queue_pauses, document_versions, quota_ledger, job_files, jobs, audit_events, sessions, otp_challenges, role_assignments, users, group_emails, group_domains, groups, settings"


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(TEST_DB)
    try:
        with eng.connect() as c:
            c.execute(text("select 1"))
    except Exception as exc:
        # Skipping suits a laptop with no database running. In CI it would turn "nothing was tested" into a pass
        if os.environ.get("PORTAL_TEST_DATABASE_REQUIRED"):
            pytest.fail(f"no test database at {TEST_DB}: {exc}", pytrace=False)
        pytest.skip(f"no test database at {TEST_DB}")
    with eng.begin() as c:
        c.execute(text("drop schema public cascade; create schema public"))
    migrate()
    # From here the app connects as portal_app, which owns no table, so the row-level security policies apply to it
    with eng.begin() as c:
        c.execute(text(APP_ROLE_SQL.read_text().replace("__APP_PASSWORD__", APP_PASSWORD)))
    os.environ["PORTAL_DATABASE_URL"] = make_url(TEST_DB).set(username="portal_app", password=APP_PASSWORD) \
        .render_as_string(hide_password=False)
    for cached in (get_portal_settings, get_engine, _sessionmaker):
        cached.cache_clear()
    yield eng
    eng.dispose()


STANDARD_SERVICE = """
    insert into services (key, kind, label, enabled, external, notice_enabled, metered, used_pages, reserved_pages,
                          confirm_before_send, sort)
    values ('standard', 'processing', 'Standard', true, false, false, true, 0, 0, false, 0)
"""


@pytest.fixture
def db(engine):
    with engine.begin() as c:
        c.execute(text(f"truncate {TABLES}, user_services, group_services, services restart identity cascade"))
        c.execute(text(STANDARD_SERVICE))  # as the migration seeds it
    with Session(engine, expire_on_commit=False) as s:
        yield s


@pytest.fixture(autouse=True)
def audit_bypass_afresh():
    """Each test sees its own audit entries for row-level security bypasses (they're recorded once per process)."""
    rls.forget_audited()


@pytest.fixture(autouse=True)
def no_seen_delay(monkeypatch):
    """Emails about a document or help request wait a moment in real use; most tests don't need that."""
    from datetime import timedelta

    from portal import notifications
    monkeypatch.setattr(notifications, "SEEN_DELAY", timedelta(0))


@pytest.fixture
def redis():
    return fakeredis.FakeRedis()


@pytest.fixture
def app(db, redis):
    application = create_app()
    application.dependency_overrides[get_rate_limiter] = lambda: RateLimiter(redis)
    get_mailer().outbox.clear()
    return application


@pytest.fixture
def make_client(app):
    clients = []

    def _make() -> TestClient:
        c = TestClient(app, headers={"Origin": ORIGIN})
        c.__enter__()
        clients.append(c)
        return c

    yield _make
    for c in clients:
        c.__exit__(None, None, None)


def sent_mail() -> list:
    """Send what's in the outbox (as the worker would) and return everything sent so far."""
    from portal import outbox
    with system_session("tests") as s:  # as the worker sends it
        outbox.send_queued(s)
    return get_mailer().outbox


def last_code(email: str) -> str:
    for mail in reversed(get_mailer().outbox):
        if mail.to == email.lower():
            return re.search(r"\b(\d{6})\b", mail.text).group(1)
    raise AssertionError(f"no code mailed to {email}")


def login(client: TestClient, email: str) -> dict:
    r = client.post("/api/v1/auth/code", json={"email": email})
    assert r.status_code == 202, r.text
    r = client.post("/api/v1/auth/code/verify", json={"email": email, "code": last_code(email)})
    assert r.status_code == 200, r.text
    body = r.json()
    client.headers["Authorization"] = f"Bearer {body['access_token']}"
    return body


def make_group(db: Session, name: str, *domains: str, **kw) -> Group:
    g = Group(name=name, **kw)
    db.add(g)
    db.flush()
    from portal.services import setup_new_group
    setup_new_group(db, g)  # the standard service, as for groups created through the API
    for d in domains:
        db.add(GroupDomain(domain=d, group_id=g.id))
    db.commit()
    db.refresh(g)
    return g


def make_user(db: Session, email: str, group: Group | None, *, role: str | None = None,
              admin_of: Group | None = None) -> User:
    u = User(email=email, group_id=group.id if group else None)
    db.add(u)
    db.flush()
    if role == "super_admin":
        db.add(RoleAssignment(user_id=u.id, role="super_admin"))
    if admin_of is not None:
        db.add(RoleAssignment(user_id=u.id, role="group_admin", group_id=admin_of.id))
    db.commit()
    db.refresh(u)
    return u
