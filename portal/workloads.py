"""The service's pods as Administration > System shows them (#398): each workload with its pods, from Prometheus.

Prometheus already reads what the cluster says about this namespace (kube-state-metrics), the kubelets' probe
results and each pod's own metrics, so the portal, which faces the internet, needs no access to the Kubernetes API.

A workload is a deployment or stateful set. Its boxes run from its minimum to its maximum pod count: an autoscaler's
(KEDA's for the workers, Kubernetes' for veraPDF and the gateway), otherwise its replicas. Each pod gets one state:

- working: busy with something (a document, a pass, a picture of music)
- idle: ready, with nothing to do
- ready: ready (a pod that doesn't say whether it's busy: the APIs, the database)
- starting: not ready yet, within its first 10 minutes
- not_ready: running but not ready after that
- failing: crash-looping, its image can't be pulled, or its liveness probe failed in the last 10 minutes
- stopping: being removed (scaling down, a new release)

A row is "waiting" when documents have waited 15 minutes or more while it's at its maximum (the engine's workers:
the only queue whose waits are timed), and "scaling" while it has fewer ready pods than it wants.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field

from portal.prometheus import Prometheus
from portal.workload_catalog import about

QUEUES = {"engine-worker": "engine", "engine-adobe-worker": "adobe", "music-reader": "music"}
WAITED_LONG_S = 15 * 60
STARTING_S = 10 * 60
BAD_WAITING = {"CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError",
               "CreateContainerError", "InvalidImageName"}
# Busy, and how much a pod can take at once, from the pods' own metrics
BUSY = ["engine_worker_busy", "portal_worker_busy", "music_reader_busy"]

Q = {
    "deploy_replicas": "kube_deployment_spec_replicas",
    "sts_replicas": "kube_statefulset_replicas",
    "hpa_min": "kube_horizontalpodautoscaler_spec_min_replicas",
    "hpa_max": "kube_horizontalpodautoscaler_spec_max_replicas",
    "hpa_target": "kube_horizontalpodautoscaler_info",
    "labels": "kube_pod_labels",
    "info": "kube_pod_info",
    "containers": "kube_pod_container_info",
    "started": "kube_pod_start_time",
    "phase": "kube_pod_status_phase == 1",
    "ready": 'kube_pod_status_ready{condition="true"}',
    "deleting": "kube_pod_deletion_timestamp",
    "restarts": "sum by (pod) (kube_pod_container_status_restarts_total)",
    "waiting": "kube_pod_container_status_waiting_reason == 1",
    "terminated": "kube_pod_container_status_last_terminated_reason == 1",
    "probe_fail_1h": 'sum by (pod, probe_type) (increase(prober_probe_total{result="failed"}[1h]))',
    "liveness_fail_10m": 'sum by (pod) (increase(prober_probe_total{probe_type="Liveness", result="failed"}[10m]))',
    "cpu": "sum by (pod) (rate(container_cpu_usage_seconds_total[5m]))",
    "memory": "sum by (pod) (container_memory_working_set_bytes)",
    "cpu_limit": 'sum by (pod) (kube_pod_container_resource_limits{resource="cpu"})',
    "memory_limit": 'sum by (pod) (kube_pod_container_resource_limits{resource="memory"})',
    "busy": "max by (pod) ({__name__=~\"" + "|".join(BUSY) + "\"})",
    "places": "max by (pod) (engine_worker_places)",
    "queue": "max by (queue) (engine_queue_length)",
    "oldest": "max(portal_queue_oldest_seconds)",
}


@dataclass
class Pod:
    name: str
    state: str
    ready: bool
    phase: str | None
    node: str | None = None
    age_s: float | None = None
    restarts: int = 0
    waiting_reason: str | None = None  # why a container isn't running now
    last_restart_reason: str | None = None  # why a container last ended: OOMKilled, Error...
    liveness_failures_1h: int = 0
    readiness_failures_1h: int = 0
    cpu_cores: float | None = None
    cpu_limit: float | None = None
    memory_bytes: float | None = None
    memory_limit: float | None = None
    busy: float | None = None
    places: float | None = None
    namespace: str | None = None
    containers: list[dict] = field(default_factory=list)  # {"name", "image"}, in name order


@dataclass
class Workload:
    name: str
    kind: str  # deployment | statefulset
    min: int
    max: int
    desired: int
    autoscaled: bool
    state: str  # ok | scaling | waiting
    queue: str | None = None
    waiting: float | None = None  # items in its queue
    oldest_s: float | None = None  # how long the oldest document has waited (the engine's workers)
    scaler: str | None = None  # keda | hpa: what sets its pod count, when something does
    about: dict | None = None  # portal/workload_catalog.py: what it is and where it comes from (#419)
    pods: list[Pod] = field(default_factory=list)


def _by(series: list[dict], *labels: str) -> dict:
    """Instant query results keyed by a label (or a tuple of labels), to their value."""
    out = {}
    for s in series:
        m = s.get("metric") or {}
        key = m.get(labels[0]) if len(labels) == 1 else tuple(m.get(x) for x in labels)
        try:
            out[key] = float(s["value"][1])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return out


def _labels(series: list[dict], key: str, label: str) -> dict:
    """Instant query results keyed by one label, to another label's value."""
    return {(s.get("metric") or {}).get(key): (s.get("metric") or {}).get(label) for s in series}


def _num(v: float | None) -> float | None:
    return None if v is None or v != v else v  # NaN: nothing


def pod_state(ready: bool, phase: str | None, age_s: float | None, deleting: bool, waiting_reason: str | None,
              liveness_fails_10m: float, busy: float | None, reports_busy: bool) -> str:
    if deleting:
        return "stopping"
    if waiting_reason in BAD_WAITING or liveness_fails_10m > 0 or phase == "Failed":
        return "failing"
    if not ready:
        return "starting" if phase == "Pending" or (age_s is not None and age_s < STARTING_S) else "not_ready"
    if reports_busy:
        return "working" if (busy or 0) > 0 else "idle"
    return "ready"


def collect(prom: Prometheus, now: float | None = None) -> list[Workload]:
    """Every workload in the namespace with its pods. Raises prometheus.Unreachable."""
    now = now or time.time()
    r = {k: prom.query(q) for k, q in Q.items()}
    replicas = {**{(n, "deployment"): v for n, v in _by(r["deploy_replicas"], "deployment").items()},
                **{(n, "statefulset"): v for n, v in _by(r["sts_replicas"], "statefulset").items()}}
    hpa_min, hpa_max = _by(r["hpa_min"], "horizontalpodautoscaler"), _by(r["hpa_max"], "horizontalpodautoscaler")
    scales = {(s["metric"].get("scaletargetref_name"), (s["metric"].get("scaletargetref_kind") or "").lower()):
              s["metric"].get("horizontalpodautoscaler") for s in r["hpa_target"] if s.get("metric")}
    app = _labels(r["labels"], "pod", "label_app")
    info = {(s.get("metric") or {}).get("pod"): s.get("metric") or {} for s in r["info"]}
    containers: dict[str, dict[str, str]] = {}
    for c in r["containers"]:
        m = c.get("metric") or {}
        if m.get("pod") and m.get("container"):
            containers.setdefault(m["pod"], {})[m["container"]] = m.get("image_spec") or m.get("image") or ""
    started, ready = _by(r["started"], "pod"), _by(r["ready"], "pod")
    phase = _labels(r["phase"], "pod", "phase")
    deleting, restarts = _by(r["deleting"], "pod"), _by(r["restarts"], "pod")
    waiting = _labels(r["waiting"], "pod", "reason")
    terminated = _labels(r["terminated"], "pod", "reason")
    probes = _by(r["probe_fail_1h"], "pod", "probe_type")
    live10 = _by(r["liveness_fail_10m"], "pod")
    cpu, mem = _by(r["cpu"], "pod"), _by(r["memory"], "pod")
    cpu_lim, mem_lim = _by(r["cpu_limit"], "pod"), _by(r["memory_limit"], "pod")
    busy, places = _by(r["busy"], "pod"), _by(r["places"], "pod")
    queue = _by(r["queue"], "queue")
    oldest = next(iter(_by(r["oldest"], "__name__").values()), None)  # one series, no labels

    out: list[Workload] = []
    for (name, kind), want in sorted(replicas.items()):
        hpa = scales.get((name, kind))
        lo = int(min(hpa_min.get(hpa, want), want)) if hpa else int(want)  # KEDA may scale to none below its HPA
        hi = int(hpa_max.get(hpa, want)) if hpa else int(want)
        w = Workload(name=name, kind=kind, min=lo, max=max(hi, lo), desired=int(want), autoscaled=bool(hpa),
                     state="ok", scaler=("keda" if hpa.startswith("keda-hpa-") else "hpa") if hpa else None,
                     about=about(name))
        for pod in sorted(p for p, a in app.items() if a == name):
            meta = info.get(pod, {})
            if meta.get("created_by_kind") == "Job":
                continue
            age = now - started[pod] if pod in started else None
            is_ready = ready.get(pod, 0) > 0
            st = pod_state(is_ready, phase.get(pod), age, pod in deleting, waiting.get(pod), live10.get(pod, 0),
                           busy.get(pod), pod in busy)
            w.pods.append(Pod(
                name=pod, state=st, ready=is_ready, phase=phase.get(pod), node=meta.get("node") or None,
                age_s=round(age) if age is not None else None, restarts=int(restarts.get(pod, 0)),
                waiting_reason=waiting.get(pod), last_restart_reason=terminated.get(pod),
                liveness_failures_1h=round(probes.get((pod, "Liveness"), 0)),
                readiness_failures_1h=round(probes.get((pod, "Readiness"), 0)),
                cpu_cores=_num(cpu.get(pod)), cpu_limit=_num(cpu_lim.get(pod)), memory_bytes=_num(mem.get(pod)),
                memory_limit=_num(mem_lim.get(pod)), busy=_num(busy.get(pod)), places=_num(places.get(pod)),
                namespace=meta.get("namespace") or None,
                containers=[{"name": n, "image": i} for n, i in sorted(containers.get(pod, {}).items())]))
        if name in QUEUES:
            w.queue = QUEUES[name]
            w.waiting = queue.get(w.queue)
            w.oldest_s = oldest if name == "engine-worker" else None
        running = [p for p in w.pods if p.state != "stopping"]
        if w.waiting and len(running) >= w.max and (w.oldest_s or 0) >= WAITED_LONG_S:
            w.state = "waiting"
        elif sum(p.ready for p in running) < w.desired:
            w.state = "scaling"
        out.append(w)
    return out


def as_dicts(workloads: list[Workload]) -> list[dict]:
    return [asdict(w) for w in workloads]
