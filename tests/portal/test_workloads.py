"""#398: the service's pods, by workload, from what Prometheus says about them."""
from __future__ import annotations

from portal import prometheus, workloads
from tests.portal.test_jobs import API, fake_engine, setup  # noqa: F401

NOW = 1_800_000_000.0


def _v(value, **labels):
    return {"metric": labels, "value": [NOW, str(value)]}


def _cluster():
    """Three workers (KEDA: 1 to 4), one working, one idle, one crash-looping; the API; a finished backup job."""
    return {
        "kube_deployment_spec_replicas": [_v(3, deployment="engine-worker"), _v(1, deployment="engine-api")],
        "kube_statefulset_replicas": [_v(1, statefulset="postgres")],
        "kube_horizontalpodautoscaler_spec_min_replicas": [_v(1, horizontalpodautoscaler="keda-hpa-engine-worker")],
        "kube_horizontalpodautoscaler_spec_max_replicas": [_v(4, horizontalpodautoscaler="keda-hpa-engine-worker")],
        "kube_horizontalpodautoscaler_info": [
            _v(1, horizontalpodautoscaler="keda-hpa-engine-worker", scaletargetref_kind="Deployment",
               scaletargetref_name="engine-worker")],
        "kube_pod_labels": [_v(1, pod=p, label_app=a) for p, a in [
            ("engine-worker-a", "engine-worker"), ("engine-worker-b", "engine-worker"),
            ("engine-worker-c", "engine-worker"), ("engine-api-a", "engine-api"), ("postgres-0", "postgres"),
            ("backup-1-x", "backup")]],
        "kube_pod_info": [_v(1, pod="engine-worker-a", node="k8s-01", created_by_kind="ReplicaSet"),
                          _v(1, pod="backup-1-x", node="k8s-02", created_by_kind="Job")],
        "kube_pod_start_time": [_v(NOW - 3600, pod=p) for p in ("engine-worker-a", "engine-worker-b",
                                                                 "engine-worker-c", "engine-api-a", "postgres-0")],
        "kube_pod_status_phase == 1": [_v(1, pod=p, phase="Running") for p in ("engine-worker-a", "engine-worker-b",
                                                                                "engine-worker-c", "engine-api-a",
                                                                                "postgres-0")],
        'kube_pod_status_ready{condition="true"}': [_v(1, pod="engine-worker-a"), _v(1, pod="engine-worker-b"),
                                                    _v(0, pod="engine-worker-c"), _v(1, pod="engine-api-a"),
                                                    _v(1, pod="postgres-0")],
        "sum by (pod) (kube_pod_container_status_restarts_total)": [_v(7, pod="engine-worker-c")],
        "kube_pod_container_status_waiting_reason == 1": [_v(1, pod="engine-worker-c", reason="CrashLoopBackOff")],
        "kube_pod_container_status_last_terminated_reason == 1": [_v(1, pod="engine-worker-c", reason="OOMKilled")],
        'sum by (pod, probe_type) (increase(prober_probe_total{result="failed"}[1h]))': [
            _v(2, pod="engine-worker-c", probe_type="Liveness")],
        "sum by (pod) (rate(container_cpu_usage_seconds_total[5m]))": [_v(1.5, pod="engine-worker-a")],
        'sum by (pod) (kube_pod_container_resource_limits{resource="memory"})': [_v(6e9, pod="engine-worker-a")],
        workloads.Q["busy"]: [_v(2, pod="engine-worker-a"), _v(0, pod="engine-worker-b"), _v(0, pod="engine-worker-c")],
        "max by (pod) (engine_worker_places)": [_v(2, pod="engine-worker-a"), _v(2, pod="engine-worker-b")],
        "max by (queue) (engine_queue_length)": [_v(5, queue="engine")],
        "max(portal_queue_oldest_seconds)": [_v(1200)],
    }


class _Prom:
    def __init__(self, answers):
        self.answers = answers

    def query(self, q):
        return self.answers.get(q, [])


def test_workloads_show_their_pods_states_and_room_to_scale():
    by = {w.name: w for w in workloads.collect(_Prom(_cluster()), now=NOW)}
    assert set(by) == {"engine-worker", "engine-api", "postgres"}  # a job's pod isn't a workload's
    ew = by["engine-worker"]
    assert (ew.min, ew.max, ew.desired, ew.autoscaled, ew.queue, ew.waiting) == (1, 4, 3, True, "engine", 5)
    states = {p.name: p.state for p in ew.pods}
    assert states == {"engine-worker-a": "working", "engine-worker-b": "idle", "engine-worker-c": "failing"}
    c = next(p for p in ew.pods if p.name == "engine-worker-c")
    assert (c.restarts, c.waiting_reason, c.last_restart_reason, c.liveness_failures_1h) == (
        7, "CrashLoopBackOff", "OOMKilled", 2)
    a = next(p for p in ew.pods if p.name == "engine-worker-a")
    assert (a.node, a.age_s, a.cpu_cores, a.memory_limit, a.busy, a.places) == ("k8s-01", 3600, 1.5, 6e9, 2, 2)
    assert ew.state == "scaling"  # wants 3, has 2 ready
    assert (by["engine-api"].min, by["engine-api"].max, by["engine-api"].pods[0].state) == (1, 1, "ready")


def test_a_full_row_with_work_waiting_long_is_marked():
    c = _cluster()
    c["kube_deployment_spec_replicas"][0] = _v(4, deployment="engine-worker")
    c["kube_pod_labels"].append(_v(1, pod="engine-worker-d", label_app="engine-worker"))
    ew = {w.name: w for w in workloads.collect(_Prom(c), now=NOW)}["engine-worker"]
    assert ew.state == "waiting" and ew.oldest_s == 1200
    c["max(portal_queue_oldest_seconds)"] = [_v(300)]  # waiting, but not long yet
    assert {w.name: w for w in workloads.collect(_Prom(c), now=NOW)}["engine-worker"].state == "scaling"


def test_pod_states():
    s = workloads.pod_state
    assert s(False, "Running", 60, False, None, 0, None, False) == "starting"
    assert s(False, "Running", 3600, False, None, 0, None, False) == "not_ready"
    assert s(False, "Pending", 3600, False, None, 0, None, False) == "starting"
    assert s(True, "Running", 3600, False, None, 1, 1, True) == "failing"  # liveness failed in the last 10 minutes
    assert s(True, "Running", 3600, True, None, 0, 1, True) == "stopping"
    assert s(False, "Pending", 60, False, "ImagePullBackOff", 0, None, False) == "failing"


def test_the_pods_endpoint_is_for_super_admins_and_says_when_prometheus_is_away(setup, monkeypatch):
    s = setup
    sup = s["c"]["super"]
    assert sup.get(f"{API}/admin/monitoring/workloads").json() == {"monitoring": False, "reachable": False,
                                                                    "error": None, "workloads": []}
    assert s["c"]["admin_a"].get(f"{API}/admin/monitoring/workloads").status_code == 403
    monkeypatch.setattr(prometheus, "get_prometheus", lambda: _Prom(_cluster()))
    r = sup.get(f"{API}/admin/monitoring/workloads").json()
    assert r["reachable"] and [w["name"] for w in r["workloads"]] == ["engine-api", "engine-worker", "postgres"]

    class Away:
        def query(self, q):
            raise prometheus.Unreachable("timed out")
    monkeypatch.setattr(prometheus, "get_prometheus", lambda: Away())
    r = sup.get(f"{API}/admin/monitoring/workloads").json()
    assert not r["reachable"] and r["error"] == "timed out"


def test_a_pod_says_its_namespace_and_containers_and_a_workload_what_it_is():
    """#419: what the pod details' sections and kubectl commands are made from."""
    c = _cluster()
    c["kube_pod_info"][0] = _v(1, pod="engine-worker-a", node="k8s-01", created_by_kind="ReplicaSet",
                               namespace="pdf-remediation")
    c["kube_pod_container_info"] = [
        _v(1, pod="engine-worker-a", container="engine-worker", image="ghcr.io/x/pdfrem-engine:abc",
           image_spec="ghcr.io/x/pdfrem-engine:abc"),
        _v(1, pod="engine-worker-a", container="sidecar", image="busybox:1")]
    by = {w.name: w for w in workloads.collect(_Prom(c), now=NOW)}
    a = next(p for p in by["engine-worker"].pods if p.name == "engine-worker-a")
    assert a.namespace == "pdf-remediation"
    assert a.containers == [{"name": "engine-worker", "image": "ghcr.io/x/pdfrem-engine:abc"},
                            {"name": "sidecar", "image": "busybox:1"}]
    assert by["engine-worker"].scaler == "keda" and by["engine-api"].scaler is None
    assert "Remediates documents" in by["engine-worker"].about["description"]
    assert by["postgres"].about["sources"][0]["url"] == "https://github.com/postgres/postgres"


def test_every_workload_in_the_manifests_is_described():
    """A Deployment or StatefulSet added to deploy/k8s/manifests needs its entry in portal/workload_catalog.py."""
    import re
    from pathlib import Path

    from portal.workload_catalog import CATALOG

    names = set()
    for f in sorted((Path(__file__).resolve().parents[2] / "deploy/k8s/manifests").glob("*.yaml")):
        for doc in f.read_text().split("\n---"):
            if re.search(r"^kind: (Deployment|StatefulSet)$", doc, re.M):
                names.add(re.search(r"^metadata:\n  name: (\S+)", doc, re.M).group(1))
    assert names and names == set(CATALOG)
    for name, entry in CATALOG.items():
        assert entry["description"].endswith(".") and entry["sources"], name
        assert all(s["url"].startswith("https://github.com/") and s["label"] for s in entry["sources"]), name
        assert isinstance(entry["shell"], bool) and (entry["port"] is None or isinstance(entry["port"], int)), name
        for s in entry["sources"]:  # this project's own files exist
            if "/blob/main/" in s["url"]:
                assert (Path(__file__).resolve().parents[2] / s["url"].split("/blob/main/")[1]).exists(), s["url"]
