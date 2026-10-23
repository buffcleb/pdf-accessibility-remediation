"""Monitoring from the admin area (#397): Administration > System's figures, charts and pods (#398), read from the
deployment's Prometheus with fixed queries, and signing super admins in to Grafana through the portal.

Grafana sits behind the web app's nginx at /grafana. Opening it from the admin area gives the browser a short-lived
cookie, signed by the portal and sent only to /grafana. On every request there, nginx asks /monitoring/grafana-auth
whether the cookie belongs to a super admin who is still one; if so it passes the request on with their email as the
user (Grafana's auth proxy), so there are no Grafana logins to manage. Removing someone's super admin role, or
disabling their account, ends their Grafana access at their next request.
"""
from __future__ import annotations

import math
import time
import uuid

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from portal import audit, prometheus, rls, workloads
from portal.config import get_portal_settings
from portal.db import get_db
from portal.deps import Principal, require_super_admin
from portal.errors import ApiError
from portal.models import User
from portal.security.tokens import constant_time_equal, keyed_hash

router = APIRouter(tags=["monitoring"])
COOKIE = "pdfrem_grafana"
COOKIE_PATH = "/grafana"
LIFETIME_S = 8 * 3600


def grafana_token(user_id: uuid.UUID, now: float | None = None) -> str:
    expires = int((now or time.time()) + LIFETIME_S)
    body = f"{user_id}.{expires}"
    return f"{body}.{keyed_hash(body, 'grafana')}"


def grafana_user(db: Session, token: str | None, now: float | None = None) -> User | None:
    """The super admin a Grafana cookie belongs to, or None (forged, expired, or no longer a super admin)."""
    try:
        uid, expires, sig = (token or "").split(".")
        if not constant_time_equal(sig, keyed_hash(f"{uid}.{expires}", "grafana")):
            return None
        if int(expires) < (now or time.time()):
            return None
        rls.identify(db, user_id=uuid.UUID(uid))  # row-level security (#350): the signature says whose it is
        user = db.get(User, uuid.UUID(uid))
    except (ValueError, TypeError):
        return None
    if user is None or user.status != "active" or user.removed_at is not None or not user.is_super_admin:
        return None
    return user


class GrafanaOut(BaseModel):
    url: str


@router.post("/admin/monitoring/grafana", response_model=GrafanaOut)
def open_grafana(request: Request, response: Response, p: Principal = Depends(require_super_admin),
                 db: Session = Depends(get_db)):
    """Let the caller into Grafana: the cookie that /grafana's requests are checked with, for eight hours."""
    s = get_portal_settings()
    if not s.grafana_url:
        raise ApiError(404, "grafana_off", "Grafana isn't part of this deployment (MONITORING=on adds it).")
    response.set_cookie(COOKIE, grafana_token(p.user.id), max_age=LIFETIME_S, path=COOKIE_PATH, httponly=True,
                        secure=s.cookie_secure, samesite="strict", domain=s.cookie_domain or None)
    audit.record(db, request, "monitoring.grafana_opened", actor=p.user, target_type="user", target_id=p.user.id)
    db.commit()
    return GrafanaOut(url=s.grafana_url)


@router.get("/monitoring/grafana-auth", include_in_schema=False)
def grafana_auth(request: Request, db: Session = Depends(get_db)):
    """For nginx's auth_request on /grafana: 200 with the user's email, or 401."""
    user = grafana_user(db, request.cookies.get(COOKIE))
    if user is None:
        return Response(status_code=401)
    return Response(status_code=200, headers={"X-Grafana-User": user.email})


# --- Administration > System ------------------------------------------------------------------------------------

# A pod's service from its name: a deployment's pods are name-<hash>-<id>, a stateful set's name-<n>
_BY_SERVICE = 'label_replace({q}, "app", "$1", "pod", "^(.+?)(?:-[a-z0-9]{{6,10}}-[a-z0-9]{{5}}|-[0-9]+)$")'
NOW = [
    ("documents", "Documents not finished", "sum(portal_documents)", ""),
    ("oldest", "Oldest waiting", "max(portal_queue_oldest_seconds)", "s"),
    ("working", "Engine documents in progress", "sum(engine_worker_busy)", ""),
    ("places", "Engine places", "sum(engine_worker_places)", ""),
    ("waiting", "Waiting for an engine worker", 'max(engine_queue_length{queue="engine"})', ""),
    ("pods", "Pods answering", 'count(up{job="pods"} == 1)', ""),
    ("restarts", "Restarts in the last hour", "sum(increase(kube_pod_container_status_restarts_total[1h]))", ""),
    ("alerts", "Alerts firing", 'count(ALERTS{alertstate="firing"})', ""),
]
# This service's containers, and each node's own totals (the root cgroup, id="/") and size (#522)
_SVC_CPU = 'rate(container_cpu_usage_seconds_total{container!=""}[5m])'
_SVC_MEM = 'container_memory_working_set_bytes{container!=""}'
_NODE_CPU = 'rate(container_cpu_usage_seconds_total{id="/"}[5m])'
_NODE_MEM = 'container_memory_working_set_bytes{id="/"}'
# Use against capacity, for the whole environment (every node) and this service: (key, label, query, of, unit)
NOW_OF = [
    ("env_cpu", "CPU in use, all nodes", f"sum({_NODE_CPU})", "sum(machine_cpu_cores)", "cores"),
    ("env_memory", "Memory in use, all nodes", f"sum({_NODE_MEM}) / 2^30", "sum(machine_memory_bytes) / 2^30", "GiB"),
    ("svc_cpu", "This service's CPU", f"sum({_SVC_CPU})", "sum(machine_cpu_cores)", "cores"),
    ("svc_memory", "This service's memory", f"sum({_SVC_MEM}) / 2^30", "sum(machine_memory_bytes) / 2^30", "GiB"),
]
# (key, title, unit, [(query, the series' name: None for the label the query sums by)])
CHARTS = [
    ("documents", "Documents not finished", "", [("sum by (status) (portal_documents)", None)]),
    ("finished", "Documents finished per hour", "",
     [("sum by (result) (increase(portal_documents_finished[1h]))", None)]),
    ("engine", "Engine documents in progress, and places", "",
     [("sum(engine_worker_busy)", "In progress"), ("sum(engine_worker_places)", "Places")]),
    ("queues", "Waiting in Redis", "", [("max by (queue) (engine_queue_length)", None)]),
    ("cpu", "CPU by service (cores)", "",
     [("sum by (app) (" + _BY_SERVICE.format(q=_SVC_CPU) + ")", None)]),
    ("memory", "Memory by service (GiB)", "",
     [("sum by (app) (" + _BY_SERVICE.format(q=_SVC_MEM) + ") / 2^30", None)]),
    # By node and in all (#522): each node's whole use against its size, this service's share of each node, and the
    # environment's use against its capacity
    ("node_cpu", "CPU by node, whole node (% of the node)", "%",
     [(f"100 * sum by (node) ({_NODE_CPU}) / sum by (node) (machine_cpu_cores)", None)]),
    ("node_memory", "Memory by node, whole node (% of the node)", "%",
     [(f"100 * sum by (node) ({_NODE_MEM}) / sum by (node) (machine_memory_bytes)", None)]),
    ("svc_node_cpu", "This service's CPU by node (cores)", "", [(f"sum by (node) ({_SVC_CPU})", None)]),
    ("svc_node_memory", "This service's memory by node (GiB)", "", [(f"sum by (node) ({_SVC_MEM}) / 2^30", None)]),
    ("env", "All nodes: CPU and memory in use (% of capacity)", "%",
     [(f"100 * sum({_NODE_CPU}) / sum(machine_cpu_cores)", "CPU"),
      (f"100 * sum({_NODE_MEM}) / sum(machine_memory_bytes)", "Memory")]),
    ("requests", "Portal requests per minute", "",
     [('sum by (class) (label_replace(rate(portal_http_requests_total[5m]), "class", "${1}xx", "status", '
       '"([0-9]).."))  * 60', None)]),
]
RANGES = {"6h": (6 * 3600, 300), "24h": (24 * 3600, 900), "7d": (7 * 86400, 7200)}
MAX_SERIES = 8


class Figure(BaseModel):
    key: str
    label: str
    value: float | None
    unit: str
    of: float | None = None  # what it's out of: "7.2 of 24 cores" (#522)


class SystemChart(BaseModel):
    key: str
    title: str
    chart: dict  # the live reports' chart shape (web/src/components/ReportChart.tsx)


class SystemOut(BaseModel):
    monitoring: bool  # the deployment has Prometheus
    reachable: bool
    grafana: bool
    error: str | None = None
    now: list[Figure] = []
    charts: list[SystemChart] = []


def _num(v) -> float | None:
    """A sample's value; None for one that isn't a number (NaN where a ratio divided by nothing)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _value(series: list[dict]) -> float | None:
    try:
        return _num(series[0]["value"][1]) if series else None
    except (KeyError, IndexError, TypeError):
        return None


def _chart(prom: prometheus.Prometheus, title: str, unit: str, queries, start: float, end: float, step: float) -> dict:
    lines: dict[str, list] = {}
    for q, fixed in queries:
        for s in prom.query_range(q, start, end, step):
            name = fixed or next(iter((s.get("metric") or {}).values()), None) or title
            lines[name] = s.get("values") or []
    # The busiest few, so a chart stays readable (Grafana has the rest)
    def peak(name):
        return max((v for v in (_num(v) for _, v in lines[name]) if v is not None), default=0)
    keep = sorted(lines, key=peak, reverse=True)[:MAX_SERIES]
    points = []
    for name in keep:
        for t, v in lines[name]:
            if (value := _num(v)) is not None:
                points.append({"t": datetime.fromtimestamp(float(t), timezone.utc).isoformat(), "series": name,
                               "value": round(value, 3)})
    return {"kind": "line", "x": "t", "x_label": "Time", "x_kind": "time", "series": "series",
            "series_label": None, "ref": False, "ref_label": None,
            "measures": [{"key": "value", "label": title, "unit": unit}], "points": points}


@router.get("/admin/monitoring/system", response_model=SystemOut)
def system(range_: Literal["6h", "24h", "7d"] = Query("6h", alias="range"),
           p: Principal = Depends(require_super_admin)):
    """What the deployment's Prometheus says now, and over the last 6 hours, day or week."""
    s = get_portal_settings()
    prom = prometheus.get_prometheus()
    out = SystemOut(monitoring=prom is not None, reachable=False, grafana=bool(s.grafana_url))
    if prom is None:
        return out
    span, step = RANGES[range_]
    end = time.time()
    try:
        out.now = [Figure(key=k, label=label, value=_value(prom.query(q)), unit=unit) for k, label, q, unit in NOW]
        out.now += [Figure(key=k, label=label, value=_value(prom.query(q)), of=_value(prom.query(of)), unit=unit)
                    for k, label, q, of, unit in NOW_OF]
        out.charts = [SystemChart(key=k, title=title, chart=_chart(prom, title, unit, qs, end - span, end, step))
                      for k, title, unit, qs in CHARTS]
        out.reachable = True
    except prometheus.Unreachable as e:
        out.error = str(e)[:300]
        out.now, out.charts = [], []
    return out


class WorkloadsOut(BaseModel):
    monitoring: bool
    reachable: bool
    error: str | None = None
    workloads: list[dict] = []  # portal/workloads.py: each workload and its pods


@router.get("/admin/monitoring/workloads", response_model=WorkloadsOut)
def pods(p: Principal = Depends(require_super_admin)):
    """Every workload and its pods, as Prometheus last saw them (#398)."""
    prom = prometheus.get_prometheus()
    out = WorkloadsOut(monitoring=prom is not None, reachable=False)
    if prom is None:
        return out
    try:
        out.workloads = workloads.as_dicts(workloads.collect(prom))
        out.reachable = True
    except prometheus.Unreachable as e:
        out.error = str(e)[:300]
    return out
