"""Every pod a network policy lets in must exist: each `podSelector` in deploy/k8s must match the labels of some pod
template in the same manifests. The migration job's policy entry named `app: portal-migrate`, a label the Job
carried but not the pod it made, so the migration could not reach Postgres once the policies were in force."""
import re
from pathlib import Path

import yaml

K8S = Path(__file__).resolve().parents[2] / "deploy" / "k8s"


def _docs():
    for path in sorted(K8S.glob("manifests/*.yaml")) + [K8S / "migrate-job.yaml"]:
        # A line that is only a placeholder holds a block of its own (replicas, or the Ingress's optional TLS lines):
        # the replicas, else nothing, as with those settings unset; any other placeholder becomes a word
        text = re.sub(r"(?m)^( *)\$\{(\w+)\} *$",
                      lambda m: f"{m.group(1)}replicas: 1" if m.group(2).endswith("_REPLICAS") else "", path.read_text())
        text = re.sub(r"\$\{(\w+)\}", "x", text)
        for doc in yaml.safe_load_all(text):
            if doc:
                yield path.name, doc


def _pod_labels():
    """The labels of every pod template, by the workload that makes it."""
    out = {}
    for name, doc in _docs():
        kind = doc["kind"]
        if kind in ("Deployment", "StatefulSet", "Job"):
            template = doc["spec"]["template"]
        elif kind == "CronJob":
            template = doc["spec"]["jobTemplate"]["spec"]["template"]
        else:
            continue
        out[f"{kind}/{doc['metadata']['name']}"] = template.get("metadata", {}).get("labels", {})
    return out


def _policy_selectors():
    for name, doc in _docs():
        if doc["kind"] != "NetworkPolicy":
            continue
        for rule in doc["spec"].get("ingress", []):
            for peer in rule.get("from", []):
                if "podSelector" in peer:
                    yield f"{name} {doc['metadata']['name']}", peer["podSelector"].get("matchLabels", {})


def test_every_policy_peer_names_a_pod_that_exists():
    pods = _pod_labels()
    unmatched = [(where, sel) for where, sel in _policy_selectors()
                 if sel and not any(sel.items() <= labels.items() for labels in pods.values())]
    assert unmatched == [], f"no pod template carries these labels: {unmatched}"


def test_the_migration_pod_carries_the_label_its_policy_entry_names():
    assert _pod_labels()["Job/portal-migrate-x"].get("app") == "portal-migrate"


def test_with_ingress_denied_by_default_every_pod_that_takes_connections_has_a_policy_letting_them_in():
    """#453: default-deny-ingress closes every pod no policy names. The jobs and cloudflared take no connections; every
    other pod must be let in by a policy of its own (the web pod from anywhere, as the way in)."""
    docs = [d for _, d in _docs()]
    assert any(d["kind"] == "NetworkPolicy" and d["metadata"]["name"] == "default-deny-ingress"
               and d["spec"]["podSelector"] == {} and "ingress" not in d["spec"] for d in docs)
    letting_in = [d["spec"]["podSelector"] for d in docs if d["kind"] == "NetworkPolicy"
                  and "Ingress" in d["spec"].get("policyTypes", []) and d["spec"].get("ingress")]

    def selected(labels, sel):
        if any(labels.get(k) != v for k, v in sel.get("matchLabels", {}).items()):
            return False
        return all(labels.get(e["key"]) in e["values"] for e in sel.get("matchExpressions", []) if e["operator"] == "In")

    for workload, labels in _pod_labels().items():
        if workload.startswith(("Job/", "CronJob/")) or workload == "Deployment/cloudflared":
            continue
        assert any(selected(labels, s) for s in letting_in), workload



def test_every_pod_sent_through_the_egress_proxy_may_reach_it():
    """With EGRESS_PROXY=enforce, the pods kept to the proxy (82-egress-enforce.yaml) must be let in by the proxy's
    own policy. ClamAV (#455) was kept to it, and sent through it in log mode too, but not let in, so its signature
    updates were dropped."""
    proxy = next(d for n, d in _docs() if d["kind"] == "NetworkPolicy" and d["metadata"]["name"] == "egress-proxy")
    allowed = {p["podSelector"]["matchLabels"]["app"] for rule in proxy["spec"]["ingress"] for p in rule["from"]}
    kept = set()
    for _, d in _docs():
        if d["kind"] == "NetworkPolicy" and d["metadata"]["name"].endswith("outbound-through-proxy"):
            for e in d["spec"]["podSelector"].get("matchExpressions", []):
                kept |= set(e["values"])
    assert {"portal-api", "litellm", "clamav"} <= kept
    assert kept <= allowed, f"kept to the proxy but not let in: {sorted(kept - allowed)}"
