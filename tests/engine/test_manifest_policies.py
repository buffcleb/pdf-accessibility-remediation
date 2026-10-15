"""Every pod a network policy lets in must exist: each `podSelector` in deploy/k8s must match the labels of some pod
template in the same manifests. The migration job's policy entry named `app: portal-migrate`, a label the Job
carried but not the pod it made, so the migration could not reach Postgres once the policies were in force."""
import re
from pathlib import Path

import yaml

K8S = Path(__file__).resolve().parents[2] / "deploy" / "k8s"


def _docs():
    for path in sorted(K8S.glob("manifests/*.yaml")) + [K8S / "migrate-job.yaml"]:
        text = re.sub(r"\$\{(\w+)\}", lambda m: "replicas: 1" if m.group(1).endswith("_REPLICAS") else "x", path.read_text())
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
