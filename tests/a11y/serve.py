"""The portal API with invented people and documents, for the accessibility checks of the web app (#369; run by
scripts/a11y.sh). Not a test itself, and never deployed: it uses the portal test suite's database and helpers, an
engine that hands back canned results (with a real tagged PDF and tag map, so the tag manager has pages to show),
and an extra endpoint that gives the sign-in code mailed to an address.

    python -m tests.a11y.serve PORT APP_ORIGIN

The database is the test suite's (PORTAL_TEST_DATABASE_URL): it's emptied first.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

import tests.portal.conftest as cf  # sets the test environment; the lines below adjust it for a browser

PORT, ORIGIN = int(sys.argv[1]), sys.argv[2]
os.environ.update({"PORTAL_CORS_ORIGINS": ORIGIN, "PORTAL_APP_URL": ORIGIN})

import fakeredis  # noqa: E402
import pymupdf as fitz  # noqa: E402
import uvicorn  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from portal import prometheus, workloads  # noqa: E402
from portal.config import get_portal_settings  # noqa: E402
from portal.engine import set_engine_client  # noqa: E402
from portal.models import Group, Job, User  # noqa: E402
from portal.security.ratelimit import RateLimiter, get_rate_limiter  # noqa: E402
from tests.portal.test_ai_tiers import _tier  # noqa: E402
from tests.portal.test_adobe import _improved_with_findings, _send  # noqa: E402
from tests.portal.test_jobs import API, FakeEngine, pdf_bytes, run_worker, topup  # noqa: E402

get_portal_settings.cache_clear()

PEOPLE = {"jordan.lee@northfield.example": "Jordan Lee", "priya.shah@northfield.example": "Priya Shah",
          "alex.morgan@northfield.example": "Alex Morgan", "sam.rivera@northfield.example": "Sam Rivera"}


class CannedPrometheus:
    """Stands in for Prometheus, so Administration > System shows pods (#398, #419): two engine workers scaled by
    KEDA, the portal's API, and the database."""

    NS = "pdf-remediation"
    DIGEST = "sha256:" + "4e61d15fd359" * 5 + "4e61"  # invented: one digest for every image
    OTHER = "sha256:" + "9b49eed8f628" * 5 + "9b49"  # what the database runs instead: System > Versions in bold (#570)
    PODS = {"engine-worker-5d9f7-abcde": "engine-worker", "engine-worker-5d9f7-fghij": "engine-worker",
            "portal-api-7c4b8-klmno": "portal-api", "postgres-0": "postgres"}

    def __init__(self) -> None:
        import time
        self.now = time.time()

    def _v(self, value, **labels):
        return {"metric": labels, "value": [self.now, str(value)]}

    def query(self, q: str) -> list[dict]:
        v, ns = self._v, self.NS
        answers = {
            "kube_deployment_spec_replicas": [v(2, deployment="engine-worker"), v(1, deployment="portal-api")],
            "kube_statefulset_replicas": [v(1, statefulset="postgres")],
            "kube_horizontalpodautoscaler_spec_min_replicas": [v(1, horizontalpodautoscaler="keda-hpa-engine-worker")],
            "kube_horizontalpodautoscaler_spec_max_replicas": [v(4, horizontalpodautoscaler="keda-hpa-engine-worker")],
            "kube_horizontalpodautoscaler_info": [v(1, horizontalpodautoscaler="keda-hpa-engine-worker",
                                                    scaletargetref_kind="Deployment",
                                                    scaletargetref_name="engine-worker")],
            "kube_pod_labels": [v(1, pod=pod, label_app=app) for pod, app in self.PODS.items()],
            "kube_pod_info": [v(1, pod=pod, node="node-1", namespace=ns, created_by_kind="ReplicaSet")
                              for pod in self.PODS],
            "kube_pod_container_info": [v(1, pod=pod, container=app, image_spec=f"example/{app}:1.0@{self.DIGEST}",
                                          image_id=f"example/{app}@{self.OTHER if app == 'postgres' else self.DIGEST}")
                                        for pod, app in self.PODS.items()],
            "kube_pod_start_time": [v(self.now - 7200, pod=pod) for pod in self.PODS],
            "kube_pod_status_phase == 1": [v(1, pod=pod, phase="Running") for pod in self.PODS],
            'kube_pod_status_ready{condition="true"}': [v(1, pod=pod) for pod in self.PODS],
            "sum by (pod) (kube_pod_container_status_restarts_total)": [v(1, pod="engine-worker-5d9f7-fghij")],
            workloads.Q["busy"]: [v(1, pod="engine-worker-5d9f7-abcde"), v(0, pod="engine-worker-5d9f7-fghij")],
            "max by (pod) (engine_worker_places)": [v(2, pod="engine-worker-5d9f7-abcde"),
                                                    v(2, pod="engine-worker-5d9f7-fghij")],
        }
        if q.startswith("sum(machine_"):  # the capacity figures (#522): 24 cores, 94.2 GiB (the query divides)
            return [v(24 if "cpu" in q else 94.2)]
        if q.startswith("sum(") and ('id="/"' in q or 'container!=""' in q):
            mine = 'container!=""' in q
            return [v((1.9 if mine else 7.234) if "cpu" in q else (9.37 if mine else 41.6))]
        return answers.get(q, [])

    def query_range(self, promql: str, start: float, end: float, step: float) -> list[dict]:
        return []

    def firing(self) -> list[dict]:
        return []


def tagged_document(work: Path) -> tuple[bytes, bytes]:
    """A small tagged PDF made by the engine's tagger (no AI, no validator), and its tag map."""
    from app.pipeline.orchestrator import prepare_analysis
    from app.tagging.tagger import tag_document
    from app.verification.tagmap import build_tagmap

    src, out = work / "guide.pdf", work / "guide_tagged.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 90), "Campus Parking Guide", fontsize=22, fontname="hebo")
    page.insert_text((72, 130), "Permits are sold by lot. Each permit is valid for one academic year.", fontsize=11)
    page.insert_text((72, 170), "Where to park", fontsize=15, fontname="hebo")
    for n, line in enumerate(["North lot: staff and visitors", "Library lot: students",
                              "Stadium lot: events and overflow"]):
        page.insert_text((90, 200 + n * 18), f"{n + 1}. {line}", fontsize=11)
    page.insert_text((72, 290), "Visitors pay at the machines by the entrance.", fontsize=11)
    doc.save(src)
    tag_document(src, out, prepare_analysis(src), title="Campus Parking Guide", language="en-US")
    return out.read_bytes(), json.dumps(build_tagmap(out)).encode()


class CannedEngine(FakeEngine):
    """The test suite's fake engine, with a real PDF and tag map for the tag manager."""

    def __init__(self, pdf: bytes, tagmap: bytes):
        super().__init__()
        self.pdf, self.tagmap = pdf, tagmap

    def fetch(self, path, out):
        data = self.pdf if path.endswith(".pdf") else self.tagmap if "tagmap" in path else None
        if data is None:
            return super().fetch(path, out)
        out.write(data)
        return len(data)

    def complete(self, jid, two_files=False):
        super().complete(jid, two_files)
        review = self.jobs[jid]["reports"]["tag_verification"]["primary"]
        review["findings"] = [
            {"severity": "high", "page": 1, "issue": "figure without alt text", "role": "Figure"},
            {"severity": "review", "page": 1, "issue": "link is described only by its URL", "role": "Link",
             "text": "https://transportation.northfield.example/permits", "action": "describe the destination"},
            {"severity": "suggest", "page": 1, "issue": "possible heading tagged as a paragraph", "role": "P",
             "text": "Where to park"},
            {"severity": "high", "page": 1, "issue": "text in the body of the page is marked as an artifact",
             "detail": "52 characters hidden from assistive technology", "boxes": [[72.0, 300.0, 420.0, 330.0]]},
        ]


def main() -> None:
    eng = create_engine(cf.TEST_DB)
    with eng.begin() as c:
        c.execute(text("drop schema public cascade; create schema public"))
    cf.migrate()
    with tempfile.TemporaryDirectory() as tmp:
        engine = CannedEngine(*tagged_document(Path(tmp)))
    set_engine_client(engine)
    canned = CannedPrometheus()
    prometheus.get_prometheus = lambda: canned
    app = cf.create_app()
    redis = fakeredis.FakeRedis()
    app.dependency_overrides[get_rate_limiter] = lambda: RateLimiter(redis)

    with Session(eng, expire_on_commit=False) as db:
        g = cf.make_group(db, "Northfield University", "northfield.example")
        cf.make_user(db, "alex.morgan@northfield.example", g, role="super_admin")
        cf.make_user(db, "priya.shah@northfield.example", g, admin_of=g)
        cf.make_user(db, "jordan.lee@northfield.example", g)
        cf.make_user(db, "sam.rivera@northfield.example", g, role="service_admin")  # #564
        for u in db.query(User).all():
            u.name = PEOPLE.get(u.email)
        db.commit()
        clients = {}
        for email in PEOPLE:
            c = TestClient(app, headers={"Origin": ORIGIN})
            c.__enter__()
            cf.login(c, email)
            clients[email] = c
        sup, admin, person = (clients[e] for e in ("alex.morgan@northfield.example", "priya.shah@northfield.example",
                                                    "jordan.lee@northfield.example"))
        topup({"c": {"super": sup}}, g.id, 500)
        tier = _tier({"c": {"super": sup, "admin_a": admin}, "a": g}, "Commercial AI", "commercial", external=True,
                     default=True, pages=300)
        jobs = {}
        for name in ("Campus_Parking_Guide_2026.pdf", "Lab_Safety_Checklist.pdf"):
            r = person.post(f"{API}/jobs", files=[("files", (name, pdf_bytes(2), "application/pdf"))],
                            data={"ai_tier": tier})
            jobs[name] = r.json()[0]["job"]["id"]
            run_worker(db)
            engine.complete(db.get(Job, uuid.UUID(jobs[name])).engine_job_id)
            run_worker(db)
        r = person.post(f"{API}/jobs", files=[("files", ("Orientation_Week.pdf", pdf_bytes(1), "application/pdf"))],
                        data={"ai_tier": tier})
        jobs["processing"] = r.json()[0]["job"]["id"]
        run_worker(db)
        # One that couldn't be processed: Needs attention's item, with its link and Acknowledge (#538)
        r = person.post(f"{API}/jobs", files=[("files", ("Old_Scanned_Flyer.pdf", pdf_bytes(1), "application/pdf"))],
                        data={"ai_tier": tier})
        run_worker(db)
        engine.fail(db.get(Job, uuid.UUID(r.json()[0]["job"]["id"])).engine_job_id, "engine_rejected")
        run_worker(db)
        person.post(f"{API}/jobs/{jobs['Lab_Safety_Checklist.pdf']}/help",
                    data={"body": "The checklist's table reads in the wrong order.", "kind": "tables"})
        # Tagged by Adobe, with the improved copy (AI alt text and fixes) as the document people get (#499)
        # (the migrations made the service, switched off: switched on for the group, with pages)
        assert sup.patch(f"{API}/admin/services/adobe", json={"enabled": True}).status_code == 200
        assert admin.patch(f"{API}/groups/{g.id}/services/adobe", json={"enabled": True, "all_users": True}).status_code == 200
        topup({"c": {"super": sup}}, g.id, 50, service="adobe")
        r = _send(person, ("Course_Catalog_Adobe.pdf", pdf_bytes(2)), ai_tier=tier)
        jobs["adobe"] = r.json()[0]["job"]["id"]
        run_worker(db)
        _improved_with_findings(engine, db.get(Job, uuid.UUID(jobs["adobe"])).engine_job_id)
        run_worker(db)
        # A subgroup (#431): a person of its own, a limit on Northfield's Standard pages, one setting of its own
        r = admin.post(f"{API}/groups/{g.id}/subgroups", json={"name": "School of Nursing"})
        sub_id = r.json()["id"]
        nurse = cf.make_user(db, "casey.nguyen@northfield.example", db.get(Group, uuid.UUID(sub_id)))
        nurse.name = "Casey Nguyen"
        db.commit()
        assert admin.patch(f"{API}/groups/{sub_id}/services/standard", json={"cap_pages": 120}).status_code == 200
        assert admin.patch(f"{API}/groups/{sub_id}", json={"show_external": True}).status_code == 200
        for c in clients.values():
            c.__exit__(None, None, None)

    @app.get("/api/v1/__last_code")
    def last_code(email: str):
        return {"code": cf.last_code(email)}

    # What the checks open: the documents, and the group
    Path(os.environ.get("A11Y_SEED_OUT", "a11y-seed.json")).write_text(json.dumps(
        {"done": jobs["Campus_Parking_Guide_2026.pdf"], "processing": jobs["processing"], "adobe": jobs["adobe"],
         "group": str(g.id), "subgroup": sub_id}))
    print("ready", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
