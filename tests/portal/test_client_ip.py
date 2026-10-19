"""Which address is recorded for a request: the proxy's header only when proxy headers are trusted, and the header
can be changed (the Cloudflare Worker front end passes the caller's address as x-client-ip)."""
from __future__ import annotations

from types import SimpleNamespace

from portal import audit


def _req(headers: dict, host: str = "192.0.2.7"):
    return SimpleNamespace(headers={k.lower(): v for k, v in headers.items()}, client=SimpleNamespace(host=host))


def _settings(monkeypatch, **kw):
    s = SimpleNamespace(**{"trust_proxy_headers": True, "client_ip_header": "cf-connecting-ip", "trusted_proxy_hops": 1,
                           "trusted_proxies": "", **kw})
    monkeypatch.setattr(audit, "get_portal_settings", lambda: s)
    return s


def test_cloudflares_header_by_default(monkeypatch):
    _settings(monkeypatch)
    assert audit.client_ip(_req({"cf-connecting-ip": "203.0.113.9", "x-client-ip": "198.51.100.1"})) == "203.0.113.9"
    assert audit.client_ip(_req({"x-forwarded-for": "198.51.100.4"})) == "198.51.100.4"
    assert audit.client_ip(_req({"cf-connecting-ip": "not-an-ip"})) is None


def test_the_header_can_be_changed(monkeypatch):
    s = _settings(monkeypatch)
    s.client_ip_header = "x-client-ip"
    assert audit.client_ip(_req({"cf-connecting-ip": "203.0.113.9", "x-client-ip": "198.51.100.1"})) == "198.51.100.1"


def test_untrusted_headers_are_ignored(monkeypatch):
    s = _settings(monkeypatch)
    s.trust_proxy_headers = False
    assert audit.client_ip(_req({"cf-connecting-ip": "203.0.113.9"})) == "192.0.2.7"


def test_forwarded_for_is_read_from_the_end_so_it_cant_be_made_up(monkeypatch):
    """A load balancer adds the address it saw at the end; whatever the caller sent stays in front of it."""
    s = _settings(monkeypatch)
    s.client_ip_header = "x-forwarded-for"
    assert audit.client_ip(_req({"x-forwarded-for": "192.0.2.99, 203.0.113.9"})) == "203.0.113.9"
    assert audit.client_ip(_req({"x-forwarded-for": "203.0.113.9"})) == "203.0.113.9"
    # ...and a made-up Cloudflare header is ignored when Cloudflare isn't the proxy
    assert audit.client_ip(_req({"cf-connecting-ip": "192.0.2.99", "x-forwarded-for": "203.0.113.9"})) == "203.0.113.9"
    # Two trusted proxies in a row (a CDN, then a load balancer): two places from the end
    s.trusted_proxy_hops = 2
    assert audit.client_ip(_req({"x-forwarded-for": "192.0.2.99, 203.0.113.9, 198.51.100.20"})) == "203.0.113.9"
    assert audit.client_ip(_req({"x-forwarded-for": "203.0.113.9"})) == "203.0.113.9"
    assert audit.client_ip(_req({})) == "192.0.2.7"


def test_headers_count_only_from_the_trusted_proxies(monkeypatch):
    """#336 (security review M10): with PORTAL_TRUSTED_PROXIES set, a caller connecting from anywhere else is known by
    their own address, whatever header they send, so they can't pick a fresh sign-in rate-limit bucket."""
    _settings(monkeypatch, trusted_proxies="10.42.0.0/16, 100.64.0.0/24")
    spoof = {"cf-connecting-ip": "203.0.113.9", "x-forwarded-for": "198.51.100.4"}
    assert audit.client_ip(_req(spoof, host="10.42.1.17")) == "203.0.113.9"  # cloudflared or the web pod
    assert audit.client_ip(_req(spoof, host="100.64.0.93")) == "203.0.113.9"  # the ingress controller's node
    assert audit.client_ip(_req(spoof, host="192.0.2.7")) == "192.0.2.7"  # straight in: its own address
    assert audit.client_ip(_req(spoof, host="not-an-address")) is None
    # A bad range is skipped, not fatal
    _settings(monkeypatch, trusted_proxies="nonsense, 10.42.0.0/16")
    assert audit.client_ip(_req(spoof, host="10.42.0.5")) == "203.0.113.9"
