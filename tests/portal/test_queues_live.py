"""The live view of the queues: every queue's items in one list, with where each has got to, narrowed by search,
group, queue or state. The per-queue lists show one queue and one state at a time."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from portal.models import Job
from tests.portal.test_jobs import API, fake_engine, pdf_bytes, run_worker, setup, topup, upload  # noqa: F401

LIVE = f"{API}/admin/queues/live"


def _live(s, **params):
    r = s["c"]["super"].get(LIVE, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _mixed(s):
    """One finished, one failed, one in progress, one waiting behind a pause; two people of one group."""
    db = s["db"]
    topup(s, s["a"].id, 200)
    ids = {}
    for client, name in (("user", "done.pdf"), ("user", "broken.pdf"), ("user2", "moving.pdf")):
        ids[name] = upload(s["c"][client], (name, pdf_bytes(2))).json()[0]["job"]["id"]
    run_worker(db)
    s["engine"].complete(db.get(Job, uuid.UUID(ids["done.pdf"])).engine_job_id)
    s["engine"].fail(db.get(Job, uuid.UUID(ids["broken.pdf"])).engine_job_id)
    run_worker(db)
    s["c"]["super"].post(f"{API}/admin/queues/service:standard/pause")
    ids["held.pdf"] = upload(s["c"]["user"], ("held.pdf", pdf_bytes(1))).json()[0]["job"]["id"]
    run_worker(db)
    return ids


def _docs(body):
    return {r["title"]: r for r in body["items"] if r["kind"] == "documents"}


def test_every_queue_in_one_list_with_where_each_item_is(setup):
    s = setup
    _mixed(s)
    body = _live(s)
    docs = _docs(body)
    assert {t: r["state"] for t, r in docs.items()} == {
        "done.pdf": "done", "broken.pdf": "failed", "moving.pdf": "active", "held.pdf": "waiting"}
    moving = docs["moving.pdf"]
    assert moving["stage"] == "tagging" and moving["who"] == "user2@a.test" and moving["group"] == "Campus A"
    assert moving["queue"] == "Standard" and moving["pages"] == 2 and moving["can_stop"] and not moving["can_cancel"]
    assert docs["held.pdf"]["detail"].startswith("Waiting for Standard") and docs["held.pdf"]["can_cancel"]
    assert docs["broken.pdf"]["detail"] and docs["done.pdf"]["stage"] is None
    # What is moving comes first, then what waits, then what has just ended
    order = [r["state"] for r in body["items"]]
    assert order.index("active") < order.index("waiting") < min(order.index("done"), order.index("failed"))
    # The emails those documents caused are in the same list
    assert any(r["kind"] == "mail" and r["queue"] == "Email" and r["group"] is None for r in body["items"])
    assert body["counts"]["active"] == 1 and body["counts"]["waiting"] >= 1 and body["recent_minutes"] == 10
    assert body["more"] is False


def test_narrowing_by_search_group_queue_and_state(setup):
    s = setup
    _mixed(s)
    assert set(_docs(_live(s, q="USER2@"))) == {"moving.pdf"}                  # a person, whatever the case
    assert set(_docs(_live(s, q="held"))) == {"held.pdf"}                      # a document
    assert _live(s, q="100%")["items"] == []                                   # "%" is a character, not a wildcard
    assert len(_docs(_live(s, group_id=str(s["a"].id)))) == 4
    other = _live(s, group_id=str(s["b"].id))
    assert other["items"] == []                                                # and no email: it belongs to no group
    assert not [r for r in _live(s, group_id=str(s["a"].id))["items"] if r["kind"] == "mail"]
    assert {r["kind"] for r in _live(s, queue="mail")["items"]} == {"mail"}
    assert set(_docs(_live(s, queue="service:standard"))) == {"done.pdf", "broken.pdf", "moving.pdf", "held.pdf"}
    only_active = _live(s, state="active")
    assert [r["title"] for r in only_active["items"]] == ["moving.pdf"]
    assert only_active["counts"]["failed"] == 1                                # the counts are of everything matched
    assert s["c"]["super"].get(LIVE, params={"state": "lost"}).status_code == 422
    assert s["c"]["super"].get(LIVE, params={"queue": "nothing"}).status_code == 404


def test_what_ended_stays_for_ten_minutes_and_no_longer(setup):
    s, db = setup, setup["db"]
    ids = _mixed(s)
    job = db.get(Job, uuid.UUID(ids["done.pdf"]))
    job.finished_at = datetime.now(timezone.utc) - timedelta(minutes=11)
    db.commit()
    assert "done.pdf" not in _docs(_live(s)) and "broken.pdf" in _docs(_live(s))
    job.finished_at = datetime.now(timezone.utc) - timedelta(minutes=9)
    db.commit()
    assert "done.pdf" in _docs(_live(s))


def test_a_row_names_the_queue_its_actions_go_to(setup):
    s = setup
    ids = _mixed(s)
    row = _docs(_live(s))["held.pdf"]
    assert row["key"] == "service:standard" and row["id"] == ids["held.pdf"]
    assert s["c"]["super"].post(f"{API}/admin/queues/{row['key']}/items/{row['id']}/cancel").status_code == 204
    assert _docs(_live(s))["held.pdf"]["state"] == "canceled"


def test_only_super_admins_see_it(setup):
    s = setup
    assert s["c"]["admin_a"].get(LIVE).status_code == 403
    assert s["c"]["user"].get(LIVE).status_code == 403
