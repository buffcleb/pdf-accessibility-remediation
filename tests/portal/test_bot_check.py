"""#68: Cloudflare Turnstile on "Email me a code": off unless configured; a token checked with siteverify."""
from __future__ import annotations

import httpx
import pytest
import respx
from sqlalchemy import select

from portal.config import get_portal_settings
from portal.mail import get_mailer
from portal.models import AuditEvent
from portal.security.botcheck import SITEVERIFY
from tests.portal.conftest import make_group

CODE = "/api/v1/auth/code"


@pytest.fixture
def on(db, monkeypatch):
    make_group(db, "A", "a.test")
    s = get_portal_settings()
    monkeypatch.setattr(s, "turnstile_site_key", "0x4AAAsite")
    monkeypatch.setattr(s, "turnstile_secret_key", "0x4AAAsecret")
    get_mailer().outbox.clear()
    return db


def _outcomes(db):
    return [e.outcome for e in db.scalars(select(AuditEvent).where(AuditEvent.action == "login.code_requested")
                                          .order_by(AuditEvent.id))]


def test_off_unless_configured(db, make_client):
    make_group(db, "A", "a.test")
    assert make_client().get("/api/v1/branding").json()["bot_check_site_key"] is None
    assert make_client().post(CODE, json={"email": "x@a.test"}).status_code == 202  # no token needed


def test_the_page_gets_the_site_key(on, make_client):
    assert make_client().get("/api/v1/branding").json()["bot_check_site_key"] == "0x4AAAsite"


def test_a_good_token_sends_the_code(on, make_client):
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(SITEVERIFY).mock(return_value=httpx.Response(200, json={"success": True}))
        r = make_client().post(CODE, json={"email": "x@a.test", "bot_check": "tok"})
    assert r.status_code == 202 and get_mailer().outbox
    sent = dict(httpx.QueryParams(route.calls[0].request.content.decode()))
    assert sent["secret"] == "0x4AAAsecret" and sent["response"] == "tok"


def test_a_bad_or_missing_token_is_refused_and_no_mail_goes(on, make_client):
    with respx.mock() as mock:
        mock.post(SITEVERIFY).mock(return_value=httpx.Response(200, json={"success": False,
                                                                           "error-codes": ["invalid-input-response"]}))
        bad = make_client().post(CODE, json={"email": "x@a.test", "bot_check": "forged"})
    missing = make_client().post(CODE, json={"email": "x@a.test"})
    for r in (bad, missing):
        assert r.status_code == 403 and r.json()["detail"]["error"] == "bot_check_failed"
    assert not get_mailer().outbox
    assert _outcomes(on) == ["bot_check_failed", "bot_check_failed"]
    details = [e.details for e in on.scalars(select(AuditEvent).where(AuditEvent.outcome == "bot_check_failed")
                                             .order_by(AuditEvent.id))]
    assert details[0]["detail"] == "invalid-input-response" and details[1]["check"] == "missing"


def test_cloudflare_unreachable_fails_open_by_default_or_closed_if_set(on, make_client, monkeypatch):
    with respx.mock() as mock:
        mock.post(SITEVERIFY).mock(side_effect=httpx.ConnectTimeout("down"))
        assert make_client().post(CODE, json={"email": "x@a.test", "bot_check": "tok"}).status_code == 202
        monkeypatch.setattr(get_portal_settings(), "turnstile_fail_open", False)
        r = make_client().post(CODE, json={"email": "y@a.test", "bot_check": "tok"})
    assert r.status_code == 403
    allowed = on.scalars(select(AuditEvent).where(AuditEvent.action == "login.code_requested",
                                                  AuditEvent.outcome == "success")).one()
    assert allowed.details == {"bot_check": "unavailable (allowed)"}


def test_signing_in_completes_with_the_check_on(on, make_client):
    """Only "Email me a code" is checked: entering the code needs no token (its own rate limit applies)."""
    from tests.portal.conftest import last_code

    c = make_client()
    with respx.mock() as mock:
        mock.post(SITEVERIFY).mock(return_value=httpx.Response(200, json={"success": True}))
        assert c.post(CODE, json={"email": "x@a.test", "bot_check": "tok"}).status_code == 202
    r = c.post("/api/v1/auth/code/verify", json={"email": "x@a.test", "code": last_code("x@a.test")})
    assert r.status_code == 200, r.text
