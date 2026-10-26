"""#456: security alerts from the audit log. Each rule fires on built audit rows, doesn't below its threshold, and
doesn't again within its window; what fires is recorded as `security.alert`, emailed to super admins and shown on
Attention."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from portal import attention, security_alerts, settings_store
from portal.models import AuditEvent, MailOutbox, Service
from tests.portal.conftest import make_group, make_user

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)


def _event(db, action, *, outcome="success", email=None, ip=None, at=None, actor=None, details=None, group=None):
    db.add(AuditEvent(action=action, outcome=outcome, actor_email=email or (actor.email if actor else None),
                      actor_id=actor.id if actor else None, ip=ip, created_at=at or NOW - timedelta(minutes=1),
                      details=details or {}, group_id=group.id if group else None))


def _fire(db, now=NOW):
    fired = security_alerts.evaluate(db, now=now)
    db.commit()
    return [(f.rule, f.subject) for f in fired]


def _recorded(db):
    return [(e.details["rule"], e.details["subject"]) for e in db.scalars(
        select(AuditEvent).where(AuditEvent.action == "security.alert").order_by(AuditEvent.id))]


def _mailed(db):
    return [(m.to_addr, m.subject) for m in db.scalars(select(MailOutbox).where(MailOutbox.kind == "alert"))]


def _setup(db):
    g = make_group(db, "Campus A", "a.test")
    root = make_user(db, "root@admin.test", None, role="super_admin")
    db.commit()
    return g, root


def test_wrong_codes_for_one_address_fire_once_per_window(db):
    _setup(db)
    for k in range(4):
        _event(db, "login", outcome="invalid_code", email="p@a.test", ip="198.51.100.7",
               at=NOW - timedelta(minutes=10 - k))
    db.commit()
    assert _fire(db) == []  # four is below five
    _event(db, "login", outcome="invalid_code", email="p@a.test", ip="198.51.100.7")
    _event(db, "login", outcome="invalid_code", email="old@a.test", at=NOW - timedelta(hours=2))  # outside the window
    db.commit()
    assert _fire(db) == [("failed_codes", "p@a.test")]
    assert _recorded(db) == [("failed_codes", "p@a.test")]
    assert _mailed(db) == [("root@admin.test", "Security alert: Repeated wrong sign-in codes for one address")]
    (alert,) = db.scalars(select(AuditEvent).where(AuditEvent.action == "security.alert"))
    assert "5 wrong sign-in codes for p@a.test" in alert.details["detail"]
    assert alert.details["ip"] == "198.51.100.7" and alert.outcome == "alert"
    # Still five in the window a minute later: not again
    assert _fire(db, NOW + timedelta(minutes=1)) == []
    # After the window, a new burst fires again
    for _ in range(5):
        _event(db, "login", outcome="invalid_code", email="p@a.test", at=NOW + timedelta(minutes=20))
    db.commit()
    assert _fire(db, NOW + timedelta(minutes=21)) == [("failed_codes", "p@a.test")]


def test_one_ip_trying_many_addresses_or_being_refused(db):
    _setup(db)
    for k in range(9):
        _event(db, "login.code_requested", outcome="not_provisioned", email=f"u{k}@a.test", ip="203.0.113.9")
    db.commit()
    assert _fire(db) == []
    _event(db, "login", outcome="invalid_code", email="U8@a.test", ip="203.0.113.9")  # the same address, any case
    db.commit()
    assert _fire(db) == []
    _event(db, "login", outcome="invalid_code", email="u9@a.test", ip="203.0.113.9")
    for _ in range(10):
        _event(db, "login.code_requested", outcome="rate_limited", email="x@a.test", ip="192.0.2.4")
    db.commit()
    assert sorted(_fire(db)) == [("ip_addresses", "203.0.113.9"), ("ip_refused", "192.0.2.4")]
    assert _mailed(db) == [("root@admin.test", "Security alerts: 2 to look at")]


def test_codes_for_unknown_addresses_over_an_hour(db):
    _setup(db)
    for k in range(19):
        _event(db, "login.code_requested", outcome="not_provisioned", email=f"n{k}@x.test", ip=f"192.0.2.{k}",
               at=NOW - timedelta(minutes=50))
    _event(db, "login.code_requested", outcome="not_provisioned", email="late@x.test", at=NOW - timedelta(hours=2))
    db.commit()
    assert _fire(db) == []
    _event(db, "login.code_requested", outcome="not_provisioned", email="n19@x.test", ip="192.0.2.99")
    db.commit()
    assert _fire(db) == [("unknown_addresses", "service")]
    assert _fire(db, NOW + timedelta(minutes=30)) == []  # within the hour


def test_one_person_downloading_many_of_other_peoples_files(db):
    g, _ = _setup(db)
    admin = make_user(db, "admin@a.test", g)
    db.commit()
    for k in range(24):
        _event(db, "file.accessed", actor=admin, details={"owner": f"o{k % 3}@a.test"}, group=g)
    db.commit()
    assert _fire(db) == []
    _event(db, "file.accessed", actor=admin, details={"owner": "o1@a.test"}, group=g)
    db.commit()
    assert _fire(db) == [("bulk_downloads", str(admin.id))]
    (alert,) = db.scalars(select(AuditEvent).where(AuditEvent.action == "security.alert"))
    assert "admin@a.test downloaded 25 files" in alert.details["detail"] and "(3 people's)" in alert.details["detail"]


def test_events_that_are_told_every_time_and_only_once(db):
    g, root = _setup(db)
    adobe = Service(key="adobe-test", kind="processing", label="Adobe", enabled=True, external=True)
    db.add(adobe)
    db.commit()
    _event(db, "session.revoked", outcome="refresh_reuse", actor=root, ip="198.51.100.1")
    _event(db, "role.granted", actor=root, details={"role": "super_admin", "email": "new@a.test"})
    _event(db, "role.granted", actor=root, details={"role": "service_admin", "email": "svc@a.test"})
    _event(db, "role.granted", actor=root, details={"role": "group_admin", "email": "ga@a.test"})  # not alerted
    _event(db, "group.service_updated", actor=root, group=g,
           details={"service": "adobe-test", "enabled": [False, True]})
    _event(db, "group.service_updated", actor=root, group=g, details={"service": "standard", "enabled": [False, True]})
    _event(db, "group.updated", actor=root, group=g, details={"local_only": [True, False]})
    _event(db, "settings.updated", actor=root, details={"file_retention_days": [30, 7]})
    _event(db, "settings.updated", actor=root, details={"record_retention_days": [2555, 3000]})  # longer: fine
    db.commit()
    assert sorted(r for r, _ in _fire(db)) == ["external_on", "external_on", "refresh_reuse", "retention_shortened",
                                               "service_admin", "super_admin"]
    # Read again on the next check (the events overlap it): not told twice
    assert _fire(db, NOW + timedelta(minutes=1)) == []
    detail = {e.details["rule"]: e.details["detail"] for e in db.scalars(
        select(AuditEvent).where(AuditEvent.action == "security.alert"))}
    assert "file retention days from 30 to 7" in detail["retention_shortened"]
    assert "made new@a.test a super admin" in detail["super_admin"]
    assert "made svc@a.test a service admin" in detail["service_admin"]


def test_attention_shows_them_for_a_day_and_they_can_be_turned_off(db, monkeypatch):
    # Attention shows the last day's against the clock: the clock is the test's (it failed once NOW was a day old)
    monkeypatch.setattr(security_alerts, "_now", lambda: NOW + timedelta(minutes=5))
    _setup(db)
    for _ in range(5):
        _event(db, "login", outcome="invalid_code", email="p@a.test")
    db.commit()
    _fire(db)
    items = [i for i in attention.attention(db) if i.kind.startswith("security:")]
    assert [(i.severity, i.title) for i in items] == [("warn", "Repeated wrong sign-in codes for one address")]
    assert items[0].fix
    rs = settings_store.load(db)
    settings_store.update(db, settings_store.RuntimeSettingsUpdate(security_alerts=False), None)
    db.commit()
    assert rs.security_alerts and not settings_store.load(db).security_alerts
    for _ in range(5):
        _event(db, "login", outcome="invalid_code", email="q@a.test", at=NOW + timedelta(minutes=30))
    db.commit()
    assert _fire(db, NOW + timedelta(minutes=31)) == []


def test_the_audit_log_can_be_searched_by_ip(db, make_client):
    from portal.live_reports import AUDIT
    assert any("host" in str(c) for c in AUDIT.search)
