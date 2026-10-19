"""#361: on the cluster the engine has no internet access, so every AI call goes through the gateway. A provider the
engine would call itself (the portal's fallback, the default judge, a review route) becomes the gateway's default
model or its judge model; deploy/k8s sets AI_GATEWAY_ONLY and the network policies that close the rest."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

from app.ai.gateway import GatewayProvider
from app.ai.registry import get_provider
from app.config import get_settings
from app.verification import ai_review

ROOT = Path(__file__).resolve().parents[2]


def _manifest(name: str) -> list[dict]:
    """A manifest as deploy.sh renders it, near enough: a line that is only a placeholder (replicas) dropped, the
    rest filled with a word."""
    text = (ROOT / "deploy/k8s/manifests" / name).read_text()
    text = re.sub(r"(?m)^\s*\$\{[A-Z_]+\}\s*$", "", text)
    return list(yaml.safe_load_all(re.sub(r"\$\{[A-Z_]+\}", "x", text)))


@pytest.fixture
def gateway_only(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "ai_gateway_only", True)
    monkeypatch.setattr(s, "gateway_default_model", "local-vision")
    monkeypatch.setattr(s, "gateway_judge_model", "local-judge")
    return s


@pytest.mark.parametrize("name", ["localai", "ollama", "openrouter"])
def test_a_direct_provider_becomes_the_gateways_default_model(gateway_only, name):
    p = get_provider(name, "qwen3-vl:8b")
    assert isinstance(p, GatewayProvider) and p.model == "local-vision"
    assert get_provider("gateway", "open-large-vision").model == "open-large-vision"
    assert get_provider("none") is None


def test_off_the_cluster_a_direct_provider_stays_direct(monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_gateway_only", False)
    assert get_provider("localai", "qwen3-vl-8b-instruct").name == "localai"


def test_the_default_judge_and_review_routes_go_to_the_gateways_judge(gateway_only, monkeypatch):
    monkeypatch.setattr(gateway_only, "verify_judge_provider", "ollama")
    monkeypatch.setattr(gateway_only, "verify_routes", json.dumps({
        "chart": {"provider": "openrouter", "model": "google/gemma-3-27b-it"},
        "table": {"provider": "gateway", "model": "open-judge"}}))
    table = ai_review.routes()
    assert table["default"] == table["chart"] == {"provider": "gateway", "model": "local-judge"}
    assert table["table"] == {"provider": "gateway", "model": "open-judge"}


def test_the_cluster_sets_it_and_closes_the_engines_egress():
    for d in (d for d in _manifest("40-engine.yaml") if d["kind"] == "Deployment"):
        env = {e["name"]: e.get("value") for e in d["spec"]["template"]["spec"]["containers"][0]["env"]}
        assert env["AI_GATEWAY_ONLY"] == "true", d["metadata"]["name"]
    policies = {p["metadata"]["name"]: p["spec"] for p in _manifest("80-network-policies.yaml")}
    egress = policies["engine-egress"]
    assert egress["policyTypes"] == ["Egress"]
    assert set(egress["podSelector"]["matchExpressions"][0]["values"]) == {"engine-api", "engine-worker", "engine-beat"}
    to = [t for rule in egress["egress"] for t in rule["to"]]
    allowed = {t.get("podSelector", {}).get("matchLabels", {}).get("app") for t in to}
    assert allowed == {None, "redis", "verapdf", "litellm"}  # None: DNS in kube-system
    assert all("ipBlock" not in t for t in to)  # nothing outside the cluster
    assert policies["music-egress"]["policyTypes"] == ["Egress"]
