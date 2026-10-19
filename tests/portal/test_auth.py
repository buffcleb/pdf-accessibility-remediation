from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import jwt
from sqlalchemy import select, text, update

from portal.mail import get_mailer
from portal.models import AuditEvent, AuthSession, OtpChallenge, User
from portal.security.tokens import ALGORITHM, signing_keys
from tests.portal.conftest import last_code, login, make_group, make_user

CODE = "/api/v1/auth/code"
VERIFY = "/api/v1/auth/code/verify"
REFRESH = "/api/v1/auth/refresh"


def test_unknown_domain_gets_configured_message_and_no_mail(db, make_client):
    c = make_client()
    r = c.post(CODE, json={"email": "someone@nowhere.test"})
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "not_provisioned"
    assert "isn't set up" in r.json()["detail"]["message"]
    assert get_mailer().outbox == []

    # Super admin changes the message
    make_group(db, "Admin", "admin.test")
    login(c, "root@admin.test")
    r = c.patch("/api/v1/admin/settings", json={"not_provisioned_message": "Use your campus address."})
    assert r.status_code == 200
    r = make_client().post(CODE, json={"email": "someone@nowhere.test"})
    assert r.json()["detail"]["message"] == "Use your campus address."


def test_domain_match_provisions_user_into_group(db, make_client):
    g = make_group(db, "Buffalo", "buffalo.test")
    c = make_client()
    body = login(c, "Jane.Doe@Buffalo.TEST")
    assert body["user"]["email"] == "jane.doe@buffalo.test"
    assert body["user"]["group_id"] == str(g.id)
    assert body["user"]["is_super_admin"] is False
    assert c.get("/api/v1/me").json()["group_name"] == "Buffalo"
    actions = [e.action for e in db.scalars(select(AuditEvent).order_by(AuditEvent.id))]
    assert actions == ["login.code_requested", "user.provisioned", "login"]


def test_group_is_fixed_at_creation(db, make_client):
    """Accounts are per email address; later domain changes don't move existing users."""
    a = make_group(db, "A", "a.test")
    login(make_client(), "x@a.test")
    db.execute(text("delete from group_domains"))
    db.commit()
    make_group(db, "B", "a.test")
    body = login(make_client(), "x@a.test")
    assert body["user"]["group_id"] == str(a.id)


def test_bootstrap_super_admin(db, make_client):
    body = login(make_client(), "root@admin.test")
    assert body["user"]["is_super_admin"] is True
    assert body["user"]["group_id"] is None


def test_wrong_code_attempts_are_limited(db, make_client):
    make_group(db, "A", "a.test")
    c = make_client()
    c.post(CODE, json={"email": "x@a.test"})
    good = last_code("x@a.test")
    bad = "000000" if good != "000000" else "111111"
    for _ in range(5):
        assert c.post(VERIFY, json={"email": "x@a.test", "code": bad}).status_code == 400
    # The code is burned after 5 wrong tries, even the right one fails now
    assert c.post(VERIFY, json={"email": "x@a.test", "code": good}).status_code == 400


def test_code_is_single_use_and_newest_only(db, make_client):
    make_group(db, "A", "a.test")
    c = make_client()
    c.post(CODE, json={"email": "x@a.test"})
    first = last_code("x@a.test")
    c.post(CODE, json={"email": "x@a.test"})
    second = last_code("x@a.test")
    if first != second:
        assert c.post(VERIFY, json={"email": "x@a.test", "code": first}).status_code == 400
    assert c.post(VERIFY, json={"email": "x@a.test", "code": second}).status_code == 200
    assert c.post(VERIFY, json={"email": "x@a.test", "code": second}).status_code == 400


def test_expired_code(db, make_client):
    make_group(db, "A", "a.test")
    c = make_client()
    c.post(CODE, json={"email": "x@a.test"})
    db.execute(update(OtpChallenge).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    db.commit()
    assert c.post(VERIFY, json={"email": "x@a.test", "code": last_code("x@a.test")}).status_code == 400


def test_codes_are_stored_hashed(db, make_client):
    make_group(db, "A", "a.test")
    make_client().post(CODE, json={"email": "x@a.test"})
    code = last_code("x@a.test")
    row = db.scalar(select(OtpChallenge))
    assert code not in row.code_hmac and len(row.code_hmac) == 64


def test_code_requests_rate_limited_per_email(db, make_client):
    make_group(db, "A", "a.test")
    c = make_client()
    for _ in range(5):
        assert c.post(CODE, json={"email": "x@a.test"}).status_code == 202
    assert c.post(CODE, json={"email": "x@a.test"}).status_code == 429


def test_refresh_rotates_and_detects_reuse(db, make_client):
    make_group(db, "A", "a.test")
    c = make_client()
    login(c, "x@a.test")
    old_cookie = c.cookies.get("portal_refresh")
    r = c.post(REFRESH)
    assert r.status_code == 200
    new_cookie = c.cookies.get("portal_refresh")
    assert new_cookie and new_cookie != old_cookie

    # Replaying the old token right away (another tab) is refused but doesn't end the session
    replay = make_client()
    replay.cookies.set("portal_refresh", old_cookie, path="/api/v1/auth")
    assert replay.post(REFRESH).status_code == 401
    assert db.scalar(select(AuthSession)).revoked_at is None

    # Replaying it later means it was stolen: the whole session ends
    db.execute(update(AuthSession).values(last_used_at=datetime.now(timezone.utc) - timedelta(minutes=5)))
    db.commit()
    replay.cookies.set("portal_refresh", old_cookie, path="/api/v1/auth")
    assert replay.post(REFRESH).status_code == 401
    db.expire_all()
    assert db.scalar(select(AuthSession)).revoked_reason == "refresh_reuse"
    assert c.post(REFRESH).status_code == 401  # the legitimate holder is signed out too


def test_refresh_refuses_foreign_origin(db, make_client):
    make_group(db, "A", "a.test")
    c = make_client()
    login(c, "x@a.test")
    r = c.post(REFRESH, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_idle_session_expires(db, make_client):
    make_group(db, "A", "a.test")
    c = make_client()
    login(c, "x@a.test")
    db.execute(update(AuthSession).values(last_used_at=datetime.now(timezone.utc) - timedelta(hours=9)))
    db.commit()
    assert c.post(REFRESH).status_code == 401


def test_logout_ends_session_immediately(db, make_client):
    make_group(db, "A", "a.test")
    c = make_client()
    login(c, "x@a.test")
    assert c.get("/api/v1/me").status_code == 200
    assert c.post("/api/v1/auth/logout").status_code == 204
    assert c.get("/api/v1/me").status_code == 401  # access token is dead too


def test_logout_all(db, make_client):
    make_group(db, "A", "a.test")
    c1, c2 = make_client(), make_client()
    login(c1, "x@a.test")
    login(c2, "x@a.test")
    assert c1.post("/api/v1/auth/logout-all").status_code == 204
    assert c2.get("/api/v1/me").status_code == 401


def test_disabled_user_cut_off_and_shown_message(db, make_client):
    g = make_group(db, "A", "a.test")
    make_user(db, "admin@a.test", g, admin_of=g)
    admin = make_client()
    login(admin, "admin@a.test")
    c = make_client()
    body = login(c, "x@a.test")
    r = admin.patch(f"/api/v1/groups/{g.id}/users/{body['user']['id']}", json={"status": "disabled"})
    assert r.status_code == 200
    assert c.get("/api/v1/me").status_code == 401
    # The same answer as anyone gets (#359); only the address's owner learns why, by email, and no code is issued
    sent_before = len(get_mailer().outbox)
    r = make_client().post(CODE, json={"email": "x@a.test"})
    assert r.status_code == 202 and r.json()["status"] == "code_sent"
    assert r.json() == make_client().post(CODE, json={"email": "new-person@a.test"}).json()
    disabled_mail = [m for m in get_mailer().outbox[sent_before:] if m.to == "x@a.test"]
    assert len(disabled_mail) == 1 and "code" not in disabled_mail[0].subject.lower()
    assert not re.search(r"\b\d{6}\b", disabled_mail[0].text)


def test_tampered_and_foreign_tokens_rejected(db, make_client):
    make_group(db, "A", "a.test")
    c = make_client()
    body = login(c, "x@a.test")
    token = body["access_token"]
    claims = jwt.decode(token, options={"verify_signature": False})
    for forged in (
        jwt.encode(claims, key="", algorithm="none"),
        jwt.encode(claims, "secret", algorithm="HS256"),
        token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB"),
        # properly signed, but by a key the portal no longer has (rotated out, or lost on restart)
        jwt.encode(claims, signing_keys()[0].private, algorithm=ALGORITHM, headers={"kid": "retired"}),
    ):
        r = make_client().get("/api/v1/me", headers={"Authorization": f"Bearer {forged}"})
        assert r.status_code == 401
    assert make_client().get("/api/v1/me").status_code == 401


def test_security_headers(db, make_client):
    r = make_client().get("/api/v1/health")
    assert r.status_code == 200
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"


def test_audit_events_are_append_only(db, make_client):
    make_group(db, "A", "a.test")
    login(make_client(), "x@a.test")
    import pytest
    from sqlalchemy.exc import DBAPIError
    with pytest.raises(DBAPIError):
        db.execute(text("update audit_events set action = 'x'"))
    db.rollback()


def test_user_cannot_exist_twice(db):
    import pytest
    from sqlalchemy.exc import IntegrityError
    g = make_group(db, "A", "a.test")
    make_user(db, "x@a.test", g)
    with pytest.raises(IntegrityError):
        db.add(User(email="x@a.test", group_id=g.id))
        db.commit()
    db.rollback()


def test_a_super_admin_can_sign_someone_out_everywhere(db, make_client):
    """#354: a lost device, or an account someone else may be using."""
    g = make_group(db, "A", "a.test")
    make_user(db, "root@admin.test", None, role="super_admin")
    make_user(db, "admin@a.test", g, admin_of=g)
    sup, admin, c1, c2 = make_client(), make_client(), make_client(), make_client()
    login(sup, "root@admin.test")
    login(admin, "admin@a.test")
    login(c1, "x@a.test")
    login(c2, "x@a.test")
    uid = db.scalar(select(User.id).where(User.email == "x@a.test"))
    assert admin.post(f"/api/v1/admin/users/{uid}/sessions/end").status_code == 403
    assert sup.post(f"/api/v1/admin/users/{uid}/sessions/end").status_code == 204
    assert c1.get("/api/v1/me").status_code == 401 and c2.get("/api/v1/me").status_code == 401
    ev = db.scalar(select(AuditEvent).where(AuditEvent.action == "sessions.ended"))
    assert ev.details["sessions"] == 2 and ev.actor_email == "root@admin.test"
    assert sup.get("/api/v1/me").status_code == 200  # nobody else is affected
