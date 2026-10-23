"""#397: Grafana for super admins through the portal: a short-lived cookie for /grafana, checked on every request
(nginx's auth_request), good only while its holder is still an active super admin."""
from __future__ import annotations

import time

from sqlalchemy import select

from portal.config import get_portal_settings
from portal.models import AuditEvent, RoleAssignment, User
from portal.routers.monitoring import COOKIE, grafana_token, grafana_user
from tests.portal.test_jobs import API, fake_engine, setup  # noqa: F401


def test_grafana_is_opened_for_super_admins_and_checked_on_every_request(setup, monkeypatch):
    s = setup
    assert s["c"]["super"].post(f"{API}/admin/monitoring/grafana").status_code == 404  # not deployed
    monkeypatch.setattr(get_portal_settings(), "grafana_url", "/grafana/")
    assert s["c"]["admin_a"].post(f"{API}/admin/monitoring/grafana").status_code == 403
    r = s["c"]["super"].post(f"{API}/admin/monitoring/grafana")
    assert r.status_code == 200 and r.json() == {"url": "/grafana/"}
    cookie = r.headers["set-cookie"]
    assert "Path=/grafana" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
    token = r.cookies.get(COOKIE)
    # nginx's check: the user's email for a good cookie, 401 for anything else
    ok = s["c"]["user"].get(f"{API}/monitoring/grafana-auth", cookies={COOKIE: token}, headers={"Authorization": ""})
    assert ok.status_code == 200 and ok.headers["x-grafana-user"] == "root@admin.test"
    for bad in (None, "", "x.y.z", token[:-1] + ("0" if token[-1] != "0" else "1")):
        cookies = {COOKIE: bad} if bad is not None else {}
        assert s["c"]["user"].get(f"{API}/monitoring/grafana-auth", cookies=cookies).status_code == 401
    assert s["db"].scalar(select(AuditEvent).where(AuditEvent.action == "monitoring.grafana_opened")) is not None


def test_a_grafana_cookie_ends_with_the_role_or_its_time(setup):
    s, db = setup, setup["db"]
    root = db.scalar(select(User).where(User.email == "root@admin.test"))
    token = grafana_token(root.id)
    assert grafana_user(db, token).id == root.id
    assert grafana_user(db, token, now=time.time() + 9 * 3600) is None          # past its eight hours
    db.query(RoleAssignment).filter_by(user_id=root.id, role="super_admin").delete()
    db.commit()
    db.expire_all()
    assert grafana_user(db, token) is None                                        # no longer a super admin


class _FakeProm:
    """Answers every query with one series per label value given, or fails."""

    def __init__(self, fail=False):
        self.fail, self.asked = fail, []

    def query(self, q):
        if self.fail:
            from portal.prometheus import Unreachable
            raise Unreachable("connection refused")
        self.asked.append(q)
        return [{"metric": {}, "value": [1, "3"]}]

    def query_range(self, q, start, end, step):
        self.asked.append(q)
        if "by (node)" in q:
            return [{"metric": {"node": n}, "values": [[start, "1"], [end, "2"]]} for n in ("node-a", "node-b")]
        if "by (app)" in q:  # more services than a chart shows: the busiest eight
            return [{"metric": {"app": f"svc{i}"}, "values": [[start, str(i)], [end, str(i)]]} for i in range(10)]
        return [{"metric": {"status": "queued"}, "values": [[start, "1"], [end, "NaN"]]}]


def test_the_system_page_reads_prometheus_with_fixed_queries(setup, monkeypatch):
    from portal import prometheus
    s = setup
    sup = s["c"]["super"]
    assert sup.get(f"{API}/admin/monitoring/system").json()["monitoring"] is False  # MONITORING=off
    assert s["c"]["admin_a"].get(f"{API}/admin/monitoring/system").status_code == 403
    fake = _FakeProm()
    monkeypatch.setattr(prometheus, "get_prometheus", lambda: fake)
    r = sup.get(f"{API}/admin/monitoring/system", params={"range": "24h"}).json()
    assert r["monitoring"] and r["reachable"] and r["error"] is None
    assert {f["key"]: f["value"] for f in r["now"]}["documents"] == 3
    charts = {c["key"]: c["chart"] for c in r["charts"]}
    assert charts["documents"]["points"] == [{"t": charts["documents"]["points"][0]["t"], "series": "queued",
                                              "value": 1.0}]  # NaN left out
    assert sorted({p["series"] for p in charts["cpu"]["points"]}) == [f"svc{i}" for i in range(2, 10)]
    assert {p["series"] for p in charts["engine"]["points"]} == {"In progress", "Places"}
    assert sup.get(f"{API}/admin/monitoring/system", params={"range": "1y"}).status_code == 422
    monkeypatch.setattr(prometheus, "get_prometheus", lambda: _FakeProm(fail=True))
    r = sup.get(f"{API}/admin/monitoring/system").json()
    assert r["reachable"] is False and "refused" in r["error"] and r["charts"] == []


def test_cpu_and_memory_by_node_and_for_the_whole_environment(setup, monkeypatch):
    """#522: each node's whole use against its size, this service's share of each node, and every node together; the
    service's own figures count its containers only, never the node-wide series kept beside them."""
    from portal import prometheus
    s = setup
    fake = _FakeProm()
    monkeypatch.setattr(prometheus, "get_prometheus", lambda: fake)
    r = s["c"]["super"].get(f"{API}/admin/monitoring/system").json()
    now = {f["key"]: f for f in r["now"]}
    assert now["env_cpu"]["value"] == 3 and now["env_cpu"]["of"] == 3 and now["env_cpu"]["unit"] == "cores"
    assert now["env_memory"]["unit"] == "GiB" and now["svc_memory"]["of"] == 3
    charts = {c["key"]: c["chart"] for c in r["charts"]}
    for key in ("node_cpu", "node_memory", "svc_node_cpu", "svc_node_memory"):
        assert {p["series"] for p in charts[key]["points"]} == {"node-a", "node-b"}, key
    assert {p["series"] for p in charts["env"]["points"]} == {"CPU", "Memory"}
    assert charts["node_cpu"]["measures"][0]["unit"] == "%"
    # Every container query is either this service's containers or a node's own total: none counts both
    container_queries = [q for q in fake.asked if "container_cpu" in q or "container_memory" in q]
    assert container_queries and all('container!=""' in q or 'id="/"' in q for q in container_queries)
    assert any("machine_cpu_cores" in q for q in fake.asked) and any("machine_memory_bytes" in q for q in fake.asked)
    # The pods view's figures count containers only too
    from portal import workloads
    assert all('container!=""' in q for k, q in workloads.Q.items() if k in ("cpu", "memory"))
