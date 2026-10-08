"""Which address is recorded for a request: the proxy's header only when proxy headers are trusted, and the header
can be changed (the Cloudflare Worker front end passes the caller's address as x-client-ip)."""
from __future__ import annotations

from types import SimpleNamespace

from portal import audit


def _req(headers: dict, host: str = "192.0.2.7"):
    return SimpleNamespace(headers={k.lower(): v for k, v in headers.items()}, client=SimpleNamespace(host=host))


def _settings(monkeypatch, **kw):
    s = SimpleNamespace(trust_proxy_headers=True, client_ip_header="cf-connecting-ip", **kw)
    monkeypatch.setattr(audit, "get_portal_settings", lambda: s)
    return s


def test_cloudflares_header_by_default(monkeypatch):
    _settings(monkeypatch)
    assert audit.client_ip(_req({"cf-connecting-ip": "203.0.113.9", "x-client-ip": "198.51.100.1"})) == "203.0.113.9"
    assert audit.client_ip(_req({"x-forwarded-for": "198.51.100.4, 192.0.2.1"})) == "198.51.100.4"
    assert audit.client_ip(_req({"cf-connecting-ip": "not-an-ip"})) is None


def test_the_header_can_be_changed(monkeypatch):
    s = _settings(monkeypatch)
    s.client_ip_header = "x-client-ip"
    assert audit.client_ip(_req({"cf-connecting-ip": "203.0.113.9", "x-client-ip": "198.51.100.1"})) == "198.51.100.1"


def test_untrusted_headers_are_ignored(monkeypatch):
    s = _settings(monkeypatch)
    s.trust_proxy_headers = False
    assert audit.client_ip(_req({"cf-connecting-ip": "203.0.113.9"})) == "192.0.2.7"
