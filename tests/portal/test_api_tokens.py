"""#518: personal API tokens for scripts. Switched on from the top down; tied to one service, AI level and kind; submit
by default, status and download if chosen; a token's owner's own documents only, never anything else."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

from portal.models import ApiToken, AuditEvent, Job, User
from tests.portal.test_ai_tiers import _tier
from tests.portal.test_jobs import API, fake_engine, pdf_bytes, run_worker, setup, topup  # noqa: F401

TOKENS = f"{API}/me/api-tokens"


def _switch_on(s, group="a"):
    assert s["c"]["super"].patch(f"{API}/admin/settings", json={"api_tokens_enabled": True}).status_code == 200
    admin = s["c"][f"admin_{group}"]
    assert admin.patch(f"{API}/groups/{s[group].id}", json={"api_tokens_enabled": True}).status_code == 200


def _make(s, client="user", **body):
    r = s["c"][client].post(TOKENS, json={"name": "lab scanner", **body})
    assert r.status_code == 201, r.text
    return r.json()


def _as(client, token):
    """The test client's own sign-in replaced by the token, as a script would send it."""
    client.headers["Authorization"] = f"Bearer {token}"
    return client


def _script(s, make_client, token):
    return _as(make_client(), token)


def _send(c, *names, **form):
    files = [("files", (n, pdf_bytes(1), "application/pdf")) for n in names]
    return c.post(f"{API}/jobs", files=files, data=form)


def _finish(s, job_id):
    run_worker(s["db"])
    s["engine"].complete(s["db"].get(Job, uuid.UUID(job_id)).engine_job_id)
    run_worker(s["db"])


def test_switched_on_from_the_top_down(setup):
    s = setup
    c = s["c"]["user"]
    assert c.get(TOKENS).json()["allowed"] is False
    assert c.post(TOKENS, json={"name": "x"}).json()["detail"]["message"] == "API tokens are switched off for this service."
    s["c"]["super"].patch(f"{API}/admin/settings", json={"api_tokens_enabled": True})
    r = c.post(TOKENS, json={"name": "x"})
    assert r.status_code == 403 and "group's administrators" in r.json()["detail"]["message"]
    s["c"]["admin_a"].patch(f"{API}/groups/{s['a'].id}", json={"api_tokens_enabled": True})
    made = _make(s)
    assert made["token"].startswith("pdfr_") and len(made["token"]) > 40
    assert made["info"]["prefix"] == made["token"][:11] and made["info"]["can_submit"] and not made["info"]["can_status"]
    # Shown once: only a keyed hash is kept, and the token is nowhere in the database or the audit log
    db = s["db"]
    row = db.get(ApiToken, uuid.UUID(made["info"]["id"]))
    assert row.token_hash != made["token"] and len(row.token_hash) == 64
    assert made["token"] not in str(c.get(TOKENS).json())
    dump = " ".join(str(r) for r in db.execute(text("select * from api_tokens")).all())
    dump += " ".join(str(e.details) for e in db.scalars(select(AuditEvent)))
    assert made["token"] not in dump
    assert db.scalars(select(AuditEvent).where(AuditEvent.action == "api_token.created")).first()


def test_a_token_submits_to_its_own_service_and_level_whatever_is_sent(setup, make_client):
    s = setup
    _switch_on(s)
    topup(s, s["a"].id, 100)
    cloud = _tier(s, "Cloud AI", "cloud-vision", metered=False, default=False)
    made = _make(s, ai_tier=cloud)
    assert made["info"]["ai_tier"] == cloud and "Cloud AI" in made["info"]["sends_to"]
    script = _script(s, make_client, made["token"])
    r = _send(script, "a.pdf", "b.pdf", processing="standard", ai_tier="something-else", per_file='{"0": {"ai_tier": "x"}}')
    assert r.status_code == 200, r.text
    jobs = [s["db"].get(Job, uuid.UUID(x["job"]["id"])) for x in r.json()]
    assert [j.ai_tier for j in jobs] == [cloud, cloud] and all(j.user.email == "user@a.test" for j in jobs)
    assert all(j.api_token_id == uuid.UUID(made["info"]["id"]) for j in jobs)
    # The person sees them in My documents, as any upload
    assert {d["filename"] for d in s["c"]["user"].get(f"{API}/documents").json()["items"]} == {"a.pdf", "b.pdf"}
    ev = s["db"].scalars(select(AuditEvent).where(AuditEvent.action == "job.submitted")).first()
    assert ev.details["api_token"] == "lab scanner"


def test_a_token_reaches_its_owners_documents_and_nothing_else(setup, make_client):
    s = setup
    _switch_on(s)
    topup(s, s["a"].id, 100)
    # Even a group admin's token, with every permission, is not an admin
    made = _make(s, client="admin_a", can_status=True, download="all")
    script = _script(s, make_client, made["token"])
    for path in ("/me", "/me/api-tokens", f"/groups/{s['a'].id}", f"/groups/{s['a'].id}/people", "/admin/people",
                 "/admin/settings", "/reports", "/admin/help/list"):
        r = script.get(f"{API}{path}")
        assert r.status_code == 403 and r.json()["detail"]["error"] == "token_not_allowed", path
    assert script.post(TOKENS, json={"name": "another"}).status_code == 403
    # Someone else's document in their group: not found, though the owner administers the group
    theirs = _send(s["c"]["user"], "theirs.pdf").json()[0]["job"]["id"]
    assert script.get(f"{API}/jobs/{theirs}").status_code == 404
    assert script.get(f"{API}/jobs/{theirs}/files.zip").status_code == 404
    assert s["c"]["admin_a"].get(f"{API}/jobs/{theirs}").status_code == 200  # (signed in, the admin may)
    assert script.get(f"{API}/documents").json()["total"] == 0


def test_permissions_submit_status_download(setup, make_client):
    s = setup
    _switch_on(s)
    topup(s, s["a"].id, 100)
    submit = _script(s, make_client, _make(s)["token"])
    job_id = _send(submit, "doc.pdf").json()[0]["job"]["id"]
    _finish(s, job_id)
    for path in ("/documents", f"/jobs/{job_id}", "/jobs"):
        r = submit.get(f"{API}{path}")
        assert r.status_code == 403 and r.json()["detail"]["error"] == "token_scope", path
    status = _script(s, make_client, _make(s, can_status=True)["token"])
    assert status.get(f"{API}/jobs/{job_id}").json()["status"] == "completed"
    assert status.get(f"{API}/documents", params={"status": "done"}).json()["total"] == 1
    files = {f["kind"]: f["id"] for f in status.get(f"{API}/jobs/{job_id}").json()["files"]}
    assert status.get(f"{API}/jobs/{job_id}/files/{files['primary']}").status_code == 403
    assert _send(status, "more.pdf").status_code == 200  # every token submits
    # Chosen files: those only, never the zip
    chosen = _script(s, make_client, _make(s, download="files", download_kinds=["primary", "report_pdf"])["token"])
    assert chosen.get(f"{API}/jobs/{job_id}/files/{files['primary']}").status_code == 200
    assert chosen.get(f"{API}/jobs/{job_id}/files/{files['report_pdf']}").status_code == 200
    assert chosen.get(f"{API}/jobs/{job_id}/files/{files['input']}").status_code == 403
    assert chosen.get(f"{API}/jobs/{job_id}/files.zip").status_code == 403
    # Everything: the zip and every file the page offers
    everything = _script(s, make_client, _make(s, download="all")["token"])
    assert everything.get(f"{API}/jobs/{job_id}/files.zip").status_code == 200
    assert everything.get(f"{API}/jobs/{job_id}/files/{files['input']}").status_code == 200
    assert s["c"]["user"].post(TOKENS, json={"name": "x", "download": "files"}).status_code == 422  # which files?


def test_a_polling_job_gets_only_what_it_hasnt_downloaded(setup, make_client):
    s = setup
    _switch_on(s)
    topup(s, s["a"].id, 100)
    submit = _script(s, make_client, _make(s)["token"])
    ids = [r["job"]["id"] for r in _send(submit, "one.pdf", "two.pdf").json()]
    poller = _script(s, make_client, _make(s, download="all")["token"])
    new = {"status": "done", "not_downloaded": "true"}
    assert poller.get(f"{API}/documents", params=new).json()["total"] == 0  # nothing finished yet
    for i in ids:
        _finish(s, i)
    waiting = poller.get(f"{API}/documents", params=new).json()
    assert {d["filename"] for d in waiting["items"]} == {"one.pdf", "two.pdf"}
    assert poller.get(f"{API}/jobs/{ids[0]}/files.zip").status_code == 200
    assert [d["filename"] for d in poller.get(f"{API}/documents", params=new).json()["items"]] == ["two.pdf"]
    # Another download job keeps its own place
    other = _script(s, make_client, _make(s, download="all")["token"])
    assert other.get(f"{API}/documents", params=new).json()["total"] == 2


def test_tokens_stop_when_revoked_expired_unused_or_switched_off(setup, make_client):
    s = setup
    _switch_on(s)
    topup(s, s["a"].id, 100)
    db = s["db"]

    def works(token):
        return _script(s, make_client, token).post(f"{API}/jobs", files=[("files", ("x.pdf", pdf_bytes(1), "application/pdf"))]).status_code == 200

    made = _make(s)
    assert works(made["token"])
    assert s["c"]["user"].delete(f"{TOKENS}/{made['info']['id']}").json()["state"] == "revoked"
    assert not works(made["token"])
    expired = _make(s)
    db.get(ApiToken, uuid.UUID(expired["info"]["id"])).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    assert not works(expired["token"])
    idle = _make(s)
    db.get(ApiToken, uuid.UUID(idle["info"]["id"])).created_at = datetime.now(timezone.utc) - timedelta(days=91)
    db.commit()
    assert not works(idle["token"])
    live = _make(s)
    assert works(live["token"])
    s["c"]["admin_a"].patch(f"{API}/groups/{s['a'].id}", json={"api_tokens_enabled": False})
    assert not works(live["token"])  # the group switched it off: at once
    s["c"]["admin_a"].patch(f"{API}/groups/{s['a'].id}", json={"api_tokens_enabled": True})
    assert works(live["token"])
    s["c"]["super"].patch(f"{API}/admin/settings", json={"api_tokens_enabled": False})
    assert not works(live["token"])  # the service switched it off
    s["c"]["super"].patch(f"{API}/admin/settings", json={"api_tokens_enabled": True})
    user = db.scalar(select(User).where(User.email == "user@a.test"))
    user.status = "disabled"
    db.commit()
    assert not works(live["token"])
    assert _script(s, make_client, "pdfr_not-a-real-token").get(f"{API}/documents").status_code == 401


def test_limits_on_tokens(setup):
    s = setup
    _switch_on(s)
    sup = s["c"]["super"]
    sup.patch(f"{API}/admin/settings", json={"api_tokens_per_person": 2, "api_token_max_days": 30})
    _make(s)
    _make(s, days=30)
    r = s["c"]["user"].post(TOKENS, json={"name": "third"})
    assert r.status_code == 409 and r.json()["detail"]["error"] == "too_many_tokens"
    first = s["c"]["user"].get(TOKENS).json()["tokens"][0]
    s["c"]["user"].delete(f"{TOKENS}/{first['id']}")
    assert s["c"]["user"].post(TOKENS, json={"name": "longer", "days": 31}).json()["detail"]["error"] == "too_long"
    assert s["c"]["user"].post(TOKENS, json={"name": "third"}).status_code == 201
    # Someone else's token can't be revoked by them
    assert s["c"]["user2"].delete(f"{TOKENS}/{first['id']}").status_code == 404
    # A level the person may not use can't be chosen
    assert s["c"]["user"].post(TOKENS, json={"name": "x", "processing": "nope"}).status_code in (403, 409, 422)


# --- part 2 ----------------------------------------------------------------------------------------------------------

def test_owners_are_told_before_expiry_and_when_an_unused_token_stops(setup):
    from portal import api_tokens
    from portal.models import MailOutbox
    s = setup
    _switch_on(s)
    db = s["db"]
    soon = _make(s, days=5)
    later = _make(s, days=60)
    first = api_tokens.sweep(db)
    assert first["tokens_warned"] == 1
    mails = db.scalars(select(MailOutbox).where(MailOutbox.kind == "api_token")).all()
    assert len(mails) == 1 and mails[0].to_addr == "user@a.test" and "expires on" in mails[0].subject
    assert soon["token"] not in mails[0].body and soon["info"]["prefix"] in mails[0].body  # never the token itself
    assert api_tokens.sweep(db)["tokens_warned"] == 0  # once
    t = db.get(ApiToken, uuid.UUID(later["info"]["id"]))
    t.created_at = datetime.now(timezone.utc) - timedelta(days=91)
    db.commit()
    assert api_tokens.sweep(db)["tokens_stopped_unused"] == 1
    db.refresh(t)
    assert t.revoked_reason == "unused" and t.idle_notified_at is not None
    stopped = [m for m in db.scalars(select(MailOutbox).where(MailOutbox.kind == "api_token")) if "stopped" in m.subject]
    assert len(stopped) == 1
    assert {x["state"] for x in s["c"]["user"].get(TOKENS).json()["tokens"] if x["id"] == later["info"]["id"]} == {"revoked"}


def test_a_groups_admins_see_and_revoke_their_peoples_tokens(setup, make_client):
    s = setup
    _switch_on(s)
    topup(s, s["a"].id, 100)
    made = _make(s)
    url = f"{API}/groups/{s['a'].id}/api-tokens"
    listed = s["c"]["admin_a"].get(url).json()
    assert [(t["name"], t["owner_email"]) for t in listed] == [("lab scanner", "user@a.test")]
    assert made["token"] not in str(listed)
    assert s["c"]["admin_b"].get(url).status_code in (403, 404)
    assert s["c"]["user"].get(url).status_code in (403, 404)
    assert s["c"]["admin_b"].delete(f"{url}/{made['info']['id']}").status_code in (403, 404)
    r = s["c"]["admin_a"].delete(f"{url}/{made['info']['id']}")
    assert r.json()["state"] == "revoked" and r.json()["revoked_reason"] == "group_admin"
    assert _send(_script(s, make_client, made["token"]), "x.pdf").status_code == 401
    ev = s["db"].scalars(select(AuditEvent).where(AuditEvent.action == "api_token.revoked")).first()
    assert ev.details["by"] == "group_admin" and ev.details["owner"] == "user@a.test"


def test_documents_say_they_were_sent_by_a_script(setup, make_client):
    s = setup
    _switch_on(s)
    topup(s, s["a"].id, 100)
    script = _script(s, make_client, _make(s)["token"])
    by_script = _send(script, "scripted.pdf").json()[0]["job"]["id"]
    _send(s["c"]["user"], "by hand.pdf")
    c = s["c"]["user"]
    assert c.get(f"{API}/jobs/{by_script}").json()["sent_by_script"] == "lab scanner"
    names = lambda params: [d["filename"] for d in c.get(f"{API}/documents", params=params).json()["items"]]  # noqa: E731
    assert names({"via": "script"}) == ["scripted.pdf"]
    assert names({"via": "web"}) == ["by hand.pdf"]
    assert c.get(f"{API}/documents", params={"via": "fax"}).status_code == 422
    group = s["c"]["admin_a"].get(f"{API}/groups/{s['a'].id}/documents", params={"via": "script"}).json()
    assert [d["filename"] for d in group["items"]] == ["scripted.pdf"]
