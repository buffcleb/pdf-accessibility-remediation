"""Email providers (#6) and branding (#5). Nothing is sent anywhere: every provider is faked."""
from __future__ import annotations

import io
import json
import smtplib

import httpx
import pytest
import respx

from portal.config import get_portal_settings
from portal.mail import Mail, MailError, get_mailer
from tests.portal.test_jobs import API, fake_engine, setup  # noqa: F401  (fixtures)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture
def backend(monkeypatch):
    """Switch the mail backend for one test; retries don't wait."""
    s = get_portal_settings()
    monkeypatch.setattr(get_mailer(), "sleep", lambda _s: None)

    def use(name, **kw):
        monkeypatch.setattr(s, "mail_backend", name)
        for k, v in kw.items():
            monkeypatch.setattr(s, k, v)
    return use


def test_plunk_sends_with_sender_name_and_retries_server_errors(backend):
    backend("plunk", plunk_api_key="sk_test", mail_from="Remediation <no-reply@example.org>",
            mail_reply_to="help@example.org")
    with respx.mock(assert_all_called=True) as rx:
        route = rx.post("https://next-api.useplunk.com/v1/send").mock(
            side_effect=[httpx.Response(503, text="busy"), httpx.Response(200, json={"success": True})])
        get_mailer().deliver(Mail("a@b.test", "Hi", "Hello <there>"), from_name="Access Team")
    assert route.call_count == 2
    sent = json.loads(route.calls.last.request.content)
    assert route.calls.last.request.headers["Authorization"] == "Bearer sk_test"
    assert sent["from"] == {"name": "Access Team", "email": "no-reply@example.org"}
    assert sent["reply"] == "help@example.org" and sent["subscribed"] is False
    assert "Hello &lt;there&gt;" in sent["body"]  # text is escaped into the HTML body


def test_cloudflare_sends_and_reports_permanent_bounces(backend):
    backend("cloudflare", cloudflare_account_id="acc1", cloudflare_api_token="tok",
            mail_from="Remediation <no-reply@example.org>")
    url = "https://api.cloudflare.com/client/v4/accounts/acc1/email/sending/send"
    with respx.mock() as rx:
        route = rx.post(url).mock(return_value=httpx.Response(200, json={
            "success": True, "result": {"delivered": ["a@b.test"], "permanent_bounces": [], "queued": []}}))
        get_mailer().deliver(Mail("a@b.test", "Hi", "Hello"))
        sent = json.loads(route.calls.last.request.content)
        assert sent == {"to": "a@b.test", "from": "no-reply@example.org", "subject": "Hi", "text": "Hello"}
        rx.post(url).mock(return_value=httpx.Response(200, json={
            "success": True, "result": {"delivered": [], "permanent_bounces": ["a@b.test"], "queued": []}}))
        with pytest.raises(MailError, match="bounces"):
            get_mailer().deliver(Mail("a@b.test", "Hi", "Hello"))


def test_client_errors_are_not_retried(backend):
    backend("plunk", plunk_api_key="sk_test")
    with respx.mock() as rx:
        route = rx.post("https://next-api.useplunk.com/v1/send").mock(return_value=httpx.Response(401, text="bad key"))
        assert get_mailer().send(Mail("a@b.test", "Hi", "x")) is False
    assert route.call_count == 1


def test_missing_provider_settings_are_reported(backend):
    backend("cloudflare", cloudflare_account_id="", cloudflare_api_token="")
    with pytest.raises(MailError, match="CLOUDFLARE"):
        get_mailer().deliver(Mail("a@b.test", "Hi", "x"))
    backend("nonsense")
    with pytest.raises(MailError, match="unknown mail backend"):
        get_mailer().deliver(Mail("a@b.test", "Hi", "x"))


def test_smtp_uses_display_name_and_retries_temporary_failures(backend, monkeypatch):
    backend("smtp", smtp_host="relay.test", smtp_port=25, mail_from="Remediation <no-reply@example.org>")
    sent, attempts = [], []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            attempts.append(host)
            if len(attempts) == 1:
                raise OSError("connection refused")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def send_message(self, msg):
            sent.append(msg)

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    get_mailer().deliver(Mail("a@b.test", "Hi", "Hello"), from_name="Access Team")
    assert len(attempts) == 2 and sent[0]["From"] == "Access Team <no-reply@example.org>"


def test_ses_sends_through_the_api(backend, monkeypatch):
    import boto3
    from botocore.stub import Stubber

    backend("ses", ses_region="us-east-1", mail_from="Remediation <no-reply@example.org>", mail_reply_to="")
    client = boto3.client("sesv2", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="y")
    stub = Stubber(client)
    stub.add_response("send_email", {"MessageId": "m1"}, {
        "FromEmailAddress": "Remediation <no-reply@example.org>",
        "Destination": {"ToAddresses": ["a@b.test"]},
        "Content": {"Simple": {"Subject": {"Data": "Hi", "Charset": "UTF-8"},
                               "Body": {"Text": {"Data": "Hello", "Charset": "UTF-8"}}}}})
    monkeypatch.setattr(boto3, "client", lambda *a, **k: client)
    with stub:
        get_mailer().deliver(Mail("a@b.test", "Hi", "Hello"))
    stub.assert_no_pending_responses()


def test_sign_in_says_when_the_code_couldnt_be_sent(setup, backend, monkeypatch):
    from portal.mail import Mailer

    def broken(self, mail, from_name=None, attempts=None):
        raise MailError("relay down", retryable=True)

    monkeypatch.setattr(Mailer, "deliver", broken)
    c = setup["c"]["user"]
    r = c.post(f"{API}/auth/code", json={"email": "user@a.test"})
    assert r.status_code == 503 and r.json()["detail"]["error"] == "mail_failed"
    log = setup["c"]["super"].get(f"{API}/admin/audit", params={"action": "login.code_not_sent"}).json()
    assert log and log[0]["outcome"] == "failure"


def test_super_admin_sends_a_test_email(setup):
    s = setup
    sup = s["c"]["super"]
    status = sup.get(f"{API}/admin/mail").json()
    assert status["backend"] == "memory"
    get_mailer().outbox.clear()
    r = sup.post(f"{API}/admin/mail/test", json={}).json()
    assert r["sent"] and r["to"] == "root@admin.test"
    assert get_mailer().outbox[-1].subject.startswith("Test email from")
    assert s["c"]["admin_a"].post(f"{API}/admin/mail/test", json={}).status_code == 403
    log = sup.get(f"{API}/admin/audit", params={"action": "mail.test_sent"}).json()
    assert log and log[0]["details"]["to"] == "root@admin.test"


def test_branding_is_public_and_edited_by_super_admins(setup, make_client):
    s = setup
    sup = s["c"]["super"]
    anon = make_client()
    b = anon.get(f"{API}/branding").json()
    assert b["service_name"] == "PDF Accessibility Remediation" and b["logo_url"] is None
    r = sup.patch(f"{API}/admin/settings", json={"organization_name": "Example Library", "accent_color": "#ffcc00"})
    assert r.status_code == 422  # too little contrast with white
    r = sup.patch(f"{API}/admin/settings", json={"organization_name": "Example Library", "accent_color": "#1F4E8C",
                                                 "contact_email": "Help@Example.org", "mail_from_name": "Example Access"})
    assert r.status_code == 200, r.text
    b = anon.get(f"{API}/branding").json()
    assert b["organization_name"] == "Example Library" and b["accent_color"] == "#1f4e8c"
    assert b["contact_email"] == "Help@example.org"
    assert sup.patch(f"{API}/admin/settings", json={"mail_from_name": "Bad <name>"}).status_code == 422
    assert s["c"]["admin_a"].patch(f"{API}/admin/settings", json={"organization_name": "X"}).status_code == 403


def test_logo_upload_checks_the_file(setup, make_client):
    sup = setup["c"]["super"]
    r = sup.put(f"{API}/admin/branding/logo", files={"file": ("logo.svg", b"<svg onload='x'/>", "image/svg+xml")})
    assert r.status_code == 415
    r = sup.put(f"{API}/admin/branding/logo", files={"file": ("big.png", PNG + b"\x00" * 600_000, "image/png")})
    assert r.status_code == 413
    r = sup.put(f"{API}/admin/branding/logo", files={"file": ("logo.png", PNG, "image/png")})
    assert r.status_code == 200 and r.json()["logo_url"].startswith("/api/v1/branding/logo?v=")
    anon = make_client()
    logo = anon.get(r.json()["logo_url"])
    assert logo.status_code == 200 and logo.content == PNG and logo.headers["content-type"] == "image/png"
    assert logo.headers["x-content-type-options"] == "nosniff"
    assert sup.delete(f"{API}/admin/branding/logo").json()["logo_url"] is None
    assert anon.get(f"{API}/branding/logo").status_code == 404


def test_emails_carry_the_branding(setup):
    s = setup
    s["c"]["super"].patch(f"{API}/admin/settings", json={"service_name": "Example Accessibility Service"})
    get_mailer().outbox.clear()
    anon = s["c"]["user2"]
    assert anon.post(f"{API}/auth/code", json={"email": "user2@a.test"}).status_code == 202
    assert "Example Accessibility Service" in get_mailer().outbox[-1].text
