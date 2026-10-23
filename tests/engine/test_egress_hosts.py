"""#453: deploy/k8s/egress_hosts.py works out which internet hosts this deployment uses, from the gateway's models,
engine.env and portal.env, and writes the egress proxy's configuration: everything let through and recorded in log
mode, only those hosts (each on its own port) in enforce mode."""
from __future__ import annotations

from pathlib import Path

import importlib.util

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("egress_hosts", ROOT / "deploy" / "k8s" / "egress_hosts.py")
eh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eh)


@pytest.fixture
def litellm(tmp_path):
    def make(text: str) -> str:
        p = tmp_path / "litellm.yaml"
        p.write_text(text)
        return str(p)
    return make


def test_the_shipped_gateway_config_needs_openrouter_bedrock_and_the_local_server():
    hosts = eh.hosts(str(ROOT / "config" / "litellm.yaml"), {"LOCAL_AI_BASE_URL": "https://ai.example.edu:8443/v1",
                                                              "AWS_REGION_NAME": "us-east-2"}, {})
    assert hosts == ["ai.example.edu:8443", "bedrock-runtime.us-east-2.amazonaws.com:443", "openrouter.ai:443"]


def test_only_what_is_in_use(litellm):
    path = litellm("model_list:\n  - model_name: x\n    litellm_params:\n      model: bedrock/converse/us.amazon.x\n")
    assert eh.hosts(path, {}, {}) == ["bedrock-runtime.us-east-1.amazonaws.com:443"]  # the region's default
    # Adobe when it's on; the portal's mail API and bot check when they're set; anything else named by hand
    got = eh.hosts(path, {"ADOBE_ENABLED": "true"}, {"PORTAL_MAIL_BACKEND": "cloudflare",
                                                     "PORTAL_TURNSTILE_SECRET_KEY": "s"}, "extra.example.org, http://h:8080")
    assert {"pdf-services-ue1.adobe.io:443", eh.ADOBE_STORAGE, "api.cloudflare.com:443", "challenges.cloudflare.com:443",
            "extra.example.org:443", "h:8080"} <= set(got)
    assert eh.hosts(path, {}, {"PORTAL_MAIL_BACKEND": "smtp"}) == ["bedrock-runtime.us-east-1.amazonaws.com:443"]
    assert "email.eu-west-1.amazonaws.com:443" in eh.hosts(path, {}, {"PORTAL_MAIL_BACKEND": "ses",
                                                                      "PORTAL_SES_REGION": "eu-west-1"})


def test_enforce_lets_through_only_the_listed_hosts_on_their_ports_and_log_lets_through_everything():
    conf = eh.squid(["openrouter.ai:443", "ai.example.edu:8443"], "enforce")
    assert "acl host0 dstdomain openrouter.ai" in conf and "acl port1 port 8443" in conf
    assert conf.rstrip().endswith("http_access deny all") and "http_access allow all" not in conf
    assert "access_log stdio:/dev/stdout" in conf and "pinger_enable off" in conf
    log = eh.squid(["openrouter.ai:443"], "log")
    assert "http_access allow all" in log and "dstdomain" not in log


def test_the_siem_over_https_is_let_through_and_syslog_isnt_a_proxy_host(litellm):
    """#456: the SIEM's HTTPS address goes through the proxy; syslog is a direct port (82-egress-enforce.yaml)."""
    path = litellm("model_list: []\n")
    url = {"PORTAL_SIEM_MODE": "https", "PORTAL_SIEM_HTTPS_URL": "https://siem.example.edu:8088/services/collector"}
    assert eh.hosts(path, {}, url) == ["siem.example.edu:8088"]
    assert eh.hosts(path, {}, {**url, "PORTAL_SIEM_MODE": "syslog"}) == []


def test_clamavs_update_host_only_with_scanning_on(litellm, monkeypatch):
    """#455: freshclam's signatures, with MALWARE_SCAN=clamav only."""
    path = litellm("model_list: []\n")
    monkeypatch.delenv("MALWARE_SCAN", raising=False)
    assert "database.clamav.net:443" not in eh.hosts(path, {}, {})
    monkeypatch.setenv("MALWARE_SCAN", "clamav")
    assert "database.clamav.net:443" in eh.hosts(path, {}, {})
