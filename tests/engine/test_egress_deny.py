"""#594: default-deny egress (EGRESS_DENY=on), as scripts/deploy.sh renders it: every workload in the manifests has an
allow-list or is listed as reaching only DNS, each allow-list holds exactly what's expected, and off renders nothing
new (in a copy of the repository's deploy files, as tests/engine/test_ingress_controllers.py does)."""
import re

import pytest
import yaml

from tests.engine.test_ingress_controllers import _render

NODES = "192.0.2.11,192.0.2.12,192.0.2.13"
# Everything optional on, so every workload is rendered
ALL_ON = {"MUSIC_READER": "on", "FORMULA_READER": "on", "MUSIC_TAG": "t", "FORMULA_TAG": "t", "MALWARE_SCAN": "clamav",
          "BACKUPS": "cronjob", "BACKUP_NFS_SERVER": "nas.example.edu", "BACKUP_NFS_PATH": "/backups",
          "AUTOSCALE": "on", "MONITORING": "on"}
WORKLOADS = ("Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob")

DNS = {"kube-dns 53/UDP", "kube-dns 53/TCP"}
INTERNET = "0.0.0.0/0 except 10.42.0.0/16,10.43.0.0/16"


def ips(port: str, cidrs: str = NODES) -> set[str]:
    return {f"{c}/32 {port}" for c in cidrs.split(",")}


# Only DNS: they answer, and open nothing themselves
DENY_ONLY = {"postgres", "redis", "redis-portal", "seaweedfs", "verapdf"}
# The portal, the gateway, the Adobe worker and ClamAV: as EGRESS_PROXY makes them (anywhere with off and log)
PROXY_USERS = {"portal-api", "portal-worker", "litellm", "engine-adobe-worker", "clamav"}
EXPECTED = {
    **{app: DNS for app in DENY_ONLY},
    **{app: {"anywhere"} for app in PROXY_USERS},
    "web": DNS | {"app=portal-api 8080/TCP", "app=grafana 3000/TCP"},
    "cloudflared": DNS | {f"{INTERNET} 7844/TCP", f"{INTERNET} 7844/UDP", f"{INTERNET} 443/TCP", "app=web 8080/TCP"},
    "prometheus": DNS | {f"any pod {p}/TCP" for p in (8000, 8080, 9121, 9187, 9464)} | ips("10250/TCP")
    | ips("6443/TCP"),
    "kube-state-metrics": DNS | ips("6443/TCP"),
    "verapdf-restart": DNS | ips("6443/TCP"),
    "postgres-exporter": DNS | {"app=postgres 5432/TCP"},
    "portal-migrate": DNS | {"app=postgres 5432/TCP"},
    "backup": DNS | {"app=postgres 5432/TCP", "app=seaweedfs 8333/TCP"},
    "redis-exporter": DNS | {"app=redis 6379/TCP"},
    "grafana": DNS | {"app=prometheus 9090/TCP"},
    # Limited before this (#361, #453), unchanged
    **{app: DNS | {"app=redis 6379/TCP", "app=verapdf 8080/TCP", "app=litellm 4000/TCP"}
       for app in ("engine-api", "engine-worker", "engine-beat")},
    **{app: DNS | {"app=redis 6379/TCP"} for app in ("music-reader", "formula-reader")},
    "egress-proxy": DNS | {f"{INTERNET} any port"},
}


def _policies(out: dict[str, list[dict]]) -> list[dict]:
    return [d for docs in out.values() for d in docs if d["kind"] == "NetworkPolicy"]


def _workloads(out: dict[str, list[dict]]) -> dict[str, str]:
    """Each workload's pod label app, by workload name."""
    found = {}
    for docs in out.values():
        for d in docs:
            if d["kind"] not in WORKLOADS:
                continue
            spec = d["spec"]["jobTemplate"]["spec"] if d["kind"] == "CronJob" else d["spec"]
            found[d["metadata"]["name"]] = spec["template"].get("metadata", {}).get("labels", {}).get("app")
    return found


def _selects(selector: dict, app: str) -> bool:
    if not selector:
        return True
    if "matchLabels" in selector:
        return selector["matchLabels"] == {"app": app}
    return all(e["key"] == "app" and e["operator"] == "In" and app in e["values"] for e in selector["matchExpressions"])


def _peer(peer: dict) -> str:
    if "ipBlock" in peer:
        block = peer["ipBlock"]
        return block["cidr"] + (" except " + ",".join(block["except"]) if block.get("except") else "")
    if peer.get("namespaceSelector") == {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}} and \
            peer.get("podSelector") == {"matchLabels": {"k8s-app": "kube-dns"}}:
        return "kube-dns"
    assert "namespaceSelector" not in peer, peer
    pods = peer["podSelector"]
    return "any pod" if not pods else ",".join(f"{k}={v}" for k, v in pods["matchLabels"].items())


def egress(policies: list[dict], app: str, leave_out: tuple[str, ...] = ()) -> set[str] | None:
    """What a pod labelled app may reach, as "destination port/protocol"; None when no policy limits it."""
    chosen = [p for p in policies if "Egress" in p["spec"]["policyTypes"] and p["metadata"]["name"] not in leave_out
              and _selects(p["spec"]["podSelector"], app)]
    if not chosen:
        return None
    allowed = set()
    for rule in (r for p in chosen for r in p["spec"].get("egress", [])):
        if not rule.get("to") and not rule.get("ports"):
            return {"anywhere"}
        peers = [_peer(t) for t in rule.get("to", [])] or ["anywhere"]
        ports = [f"{p['port']}/{p.get('protocol', 'TCP')}" for p in rule.get("ports", [])] or ["any port"]
        allowed |= {f"{peer} {port}" for peer in peers for port in ports}
    return allowed


@pytest.fixture(scope="module")
def on(tmp_path_factory):
    return _render(tmp_path_factory.mktemp("on"), "nginx", "cluster", "*.yaml", EGRESS_DENY="on", NODE_CIDRS=NODES,
                   **ALL_ON)


def test_every_workload_has_an_allow_list_or_reaches_only_dns(on):
    workloads = _workloads(on)
    assert None not in workloads.values(), f"a workload's pods have no app label: {workloads}"
    assert set(workloads.values()) == set(EXPECTED), "a workload with no entry here (or one that's gone)"
    policies = _policies(on)
    for app in workloads.values():
        assert egress(policies, app) == EXPECTED[app], app
    # Each pod an allow-list names exists
    named = {peer["podSelector"]["matchLabels"]["app"] for p in policies for r in p["spec"].get("egress", [])
             for peer in r.get("to", []) if peer.get("podSelector", {}).get("matchLabels", {}).get("app")}
    assert named <= set(workloads.values()), named - set(workloads.values())


def test_the_namespace_is_denied_by_default_and_every_pod_may_ask_dns(on):
    policies = {p["metadata"]["name"]: p["spec"] for p in _policies(on)}
    assert policies["default-deny-egress"] == {"podSelector": {}, "policyTypes": ["Egress"]}
    assert policies["dns-egress"]["podSelector"] == {}
    # A pod no policy names reaches DNS and nothing else
    assert egress(_policies(on), "something-new") == DNS


def test_they_can_be_removed_together(on):
    """deploy.sh removes them by their label with EGRESS_DENY=off (and so can someone in a hurry)."""
    for name in ("83-egress-deny.yaml", "84-egress-direct.yaml"):
        for policy in on[name]:
            assert policy["metadata"]["labels"] == {"pdfrem/egress-deny": "on"}, policy["metadata"]["name"]


def test_with_enforce_the_proxys_users_reach_out_only_through_it(tmp_path):
    out = _render(tmp_path, "nginx", "cluster", "8*.yaml", EGRESS_DENY="on", NODE_CIDRS=NODES, EGRESS_PROXY="enforce")
    policies = _policies(out)
    # deploy.sh removes direct-egress once 82-egress-enforce.yaml is in place
    for app in ("litellm", "engine-adobe-worker", "clamav"):
        assert egress(policies, app, leave_out=("direct-egress",)) == DNS | {"any pod any port"}, app
    for app in ("portal-api", "portal-worker"):
        assert egress(policies, app, leave_out=("direct-egress",)) == DNS | {"any pod any port", f"{INTERNET} 587/TCP"}


def test_the_worker_front_ends_tunnel_reaches_the_portal_api_and_the_api_its_own_addresses(tmp_path):
    out = _render(tmp_path, "nginx", "worker", "8*.yaml", EGRESS_DENY="on", NODE_CIDRS="198.51.100.0/24",
                  KUBE_API_CIDRS="192.0.2.10", KUBE_API_PORT="443")
    policies = _policies(out)
    assert "app=portal-api 8080/TCP" in egress(policies, "cloudflared")
    assert "app=web 8080/TCP" not in egress(policies, "cloudflared")
    assert egress(policies, "kube-state-metrics") == DNS | {"192.0.2.10/32 443/TCP"}
    assert "198.51.100.0/24 10250/TCP" in egress(policies, "prometheus")


def test_off_renders_nothing_new(on, tmp_path):
    off = _render(tmp_path, "nginx", "cluster", "*.yaml", **ALL_ON)
    assert set(on) - set(off) == {"83-egress-deny.yaml", "84-egress-direct.yaml"}
    assert set(off) <= set(on)
    def same(docs):  # the migration job's name has the time in it
        return re.sub(r"\d{14}", "RUN_ID", yaml.safe_dump_all(docs))
    for name in off:
        assert same(off[name]) == same(on[name]), name
    assert not any(p["metadata"]["name"] in ("default-deny-egress", "direct-egress") for p in _policies(off))


@pytest.mark.parametrize("settings, message", [
    ({}, "EGRESS_DENY=on needs NODE_CIDRS"),
    ({"NODE_CIDRS": "198.51.100.0/24,nodes"}, 'NODE_CIDRS is a comma-separated list of IPv4 addresses or ranges, not "nodes"'),
    ({"NODE_CIDRS": "198.51.100.0/24", "KUBE_API_PORT": "https"}, "KUBE_API_PORT is a port number"),
])
def test_on_without_the_nodes_addresses_is_refused(tmp_path, settings, message):
    with pytest.raises(AssertionError, match=message):
        _render(tmp_path, "nginx", "cluster", EGRESS_DENY="on", **settings)


def test_an_unknown_mode_is_refused(tmp_path):
    with pytest.raises(AssertionError, match="EGRESS_DENY is off or on"):
        _render(tmp_path, "nginx", "cluster", EGRESS_DENY="log")
