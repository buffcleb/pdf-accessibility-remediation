"""#481: a zip of many documents' files from My documents (or a group's documents), made on the fly: exactly the files
each person's document page offers them, the newest saved version beside the original output, documents whose files
are gone listed in README.txt, a cap from the Limits settings, its own rate limit, and audited."""
from __future__ import annotations

import io
import uuid
import zipfile
from datetime import datetime, timezone

from sqlalchemy import select

from portal import export, worker
from portal.models import AuditEvent, DocumentVersion, Job
from portal.storage import get_storage
from tests.portal.test_jobs import API, fake_engine, pdf_bytes, run_worker, setup, topup, upload  # noqa: F401
from tests.portal.test_versions import EDITS

MINE = f"{API}/documents/export"


def _done(s, client, name, pages=1):
    job_id = upload(s["c"][client], (name, pdf_bytes(pages))).json()[0]["job"]["id"]
    run_worker(s["db"])
    s["engine"].complete(s["db"].get(Job, uuid.UUID(job_id)).engine_job_id)
    run_worker(s["db"])
    return job_id


def _zip(r) -> zipfile.ZipFile:
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip" and "attachment" in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert z.testzip() is None  # every file's CRC checks out
    return z


def _files(z: zipfile.ZipFile, folder_part: str) -> set[str]:
    return {n.split("/", 1)[1] for n in z.namelist() if "/" in n and folder_part in n.split("/", 1)[0]}


def test_each_role_gets_exactly_the_files_the_document_page_offers(setup):
    s = setup
    topup(s, s["a"].id, 100)
    job_id = _done(s, "user", "syllabus.pdf")
    page = {who: {f["name"] for f in s["c"][who].get(f"{API}/jobs/{job_id}").json()["files"]
                  if f["kind"] not in export.STAFF_ONLY_FILES or who == "super"} for who in ("user", "admin_a", "super")}
    by_kind = {f.kind: f.name for f in s["db"].get(Job, uuid.UUID(job_id)).files}
    # The person: their own documents' files, never the engine's JSON and Markdown reports
    z = _zip(s["c"]["user"].post(MINE, json={"ids": [job_id]}))
    mine = _files(z, "syllabus")
    assert mine == page["user"] and by_kind["report_json"] not in mine and by_kind["report_md"] not in mine
    assert by_kind["input"] in mine and by_kind["primary"] in mine and by_kind["report_pdf"] in mine
    folder = z.namelist()[0].split("/")[0]
    created = s["db"].get(Job, uuid.UUID(job_id)).created_at
    assert folder == f"{created:%Y-%m-%d} syllabus"
    # What's in the zip is the file itself
    f = next(f for f in s["db"].get(Job, uuid.UUID(job_id)).files if f.kind == "primary")
    assert z.read(f"{folder}/{f.name}") == get_storage().objects[f.s3_key][0]
    # PDFs are stored as they are; text is deflated
    infos = {i.filename.split("/")[-1]: i for i in z.infolist()}
    assert infos[f.name].compress_type == zipfile.ZIP_STORED and infos["README.txt"].compress_type == zipfile.ZIP_DEFLATED
    # A group admin: the same as the page offers them (no JSON or Markdown either); a super admin: those too
    group = f"{API}/groups/{s['a'].id}/documents/export"
    assert _files(_zip(s["c"]["admin_a"].post(group, json={"ids": [job_id]})), "syllabus") == page["admin_a"]
    staff = _files(_zip(s["c"]["super"].post(group, json={"ids": [job_id]})), "syllabus")
    assert staff == page["super"] | {by_kind["report_md"]} and by_kind["report_json"] in staff


def test_only_documents_the_person_may_download(setup):
    s = setup
    topup(s, s["a"].id, 100)
    mine = _done(s, "user", "mine.pdf")
    theirs = _done(s, "user2", "theirs.pdf")
    # Someone else's id in a person's own export is simply not theirs
    z = _zip(s["c"]["user"].post(MINE, json={"ids": [mine, theirs]}))
    assert {n.split("/")[0] for n in z.namelist()} == {f"{datetime.now(timezone.utc):%Y-%m-%d} mine", "README.txt"}
    assert s["c"]["user"].post(MINE, json={"ids": [theirs]}).status_code == 422  # nothing to export
    # Another group's admin, and a member, get no group export
    group = f"{API}/groups/{s['a'].id}/documents/export"
    assert s["c"]["admin_b"].post(group, json={"ids": [mine]}).status_code in (403, 404)
    assert s["c"]["user"].post(group, json={"ids": [mine]}).status_code in (403, 404)
    assert s["c"]["user"].post(f"{group}/plan", json={"ids": [mine]}).status_code in (403, 404)
    # A deleted document is in nobody's list
    s["db"].get(Job, uuid.UUID(mine)).deleted_at = datetime.now(timezone.utc)
    s["db"].commit()
    assert s["c"]["admin_a"].post(group, json={"ids": [mine]}).status_code == 422


def test_everything_shown_follows_the_lists_filters_and_the_cap_is_a_setting(setup):
    s = setup
    topup(s, s["a"].id, 100)
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        _done(s, "user", name)
    upload(s["c"]["user"], ("waiting.pdf", pdf_bytes(1)))
    c = s["c"]["user"]
    plan = c.post(f"{MINE}/plan", json={"filters": {"status": "done"}}).json()
    assert (plan["matched"], plan["documents"], plan["limit"]) == (3, 3, 25) and plan["files"] > 3 and plan["size_bytes"] > 0
    z = _zip(c.post(MINE, json={"filters": {"status": "done", "q": "b"}}))
    assert [n.split("/")[0].split(" ", 1)[1] for n in z.namelist() if "/" in n][0] == "b"
    assert len({n.split("/")[0] for n in z.namelist() if "/" in n}) == 1
    # The cap: Administration › Settings › Limits
    r = s["c"]["super"].patch(f"{API}/admin/settings", json={"max_export_documents": 2})
    assert r.status_code == 200, r.text
    plan = c.post(f"{MINE}/plan", json={"filters": {}}).json()
    assert plan["matched"] == 4 and plan["limit"] == 2
    r = c.post(MINE, json={"filters": {}})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "too_many_documents"
    ids = [d["id"] for d in c.get(f"{API}/documents").json()["items"]]
    assert c.post(MINE, json={"ids": ids}).status_code == 422
    assert c.post(MINE, json={"ids": ids[:2]}).status_code == 200
    assert c.post(MINE, json={}).status_code == 422  # neither chosen documents nor the list


def test_saved_versions_go_in_beside_the_original_output(setup):
    s = setup
    topup(s, s["a"].id, 100)
    job_id = _done(s, "user", "doc.pdf", 2)
    db = s["db"]
    for _ in range(2):
        r = s["c"]["user"].post(f"{API}/jobs/{job_id}/versions", json={"based_on": 1, "edits": EDITS})
        assert r.status_code == 201, r.text
        worker.submit_versions(db)
        ver = db.scalar(select(DocumentVersion).where(DocumentVersion.number == r.json()["number"]))
        s["engine"].complete_edit(ver.engine_job_id)
        worker.poll_versions(db)
    newest = s["c"]["user"].get(f"{API}/jobs/{job_id}/versions").json()["versions"][-1]
    names = _files(_zip(s["c"]["user"].post(MINE, json={"ids": [job_id]})), "doc")
    assert newest["pdf_name"] in names
    older = s["c"]["user"].get(f"{API}/jobs/{job_id}/versions").json()["versions"][-2]
    assert older["kind"] == "reviewed" and older["pdf_name"] not in names  # only the newest
    assert any(n.endswith(".pdf") and n != newest["pdf_name"] for n in names)  # the original output stays


def test_files_gone_are_listed_not_included_and_names_dont_collide(setup):
    s = setup
    topup(s, s["a"].id, 100)
    first = _done(s, "user", "notes.pdf")
    second = _done(s, "user", "notes.pdf")
    gone = _done(s, "user", "old.pdf")
    db = s["db"]
    db.get(Job, uuid.UUID(gone)).files_deleted_at = datetime(2026, 9, 1, tzinfo=timezone.utc)
    db.commit()
    plan = s["c"]["user"].post(f"{MINE}/plan", json={"ids": [first, second, gone]}).json()
    assert plan["documents"] == 2 and plan["left_out"][0]["filename"] == "old.pdf"
    z = _zip(s["c"]["user"].post(MINE, json={"ids": [first, second, gone]}))
    folders = sorted({n.split("/")[0] for n in z.namelist() if "/" in n})
    assert len(folders) == 2 and folders[1] == f"{folders[0]} (2)"  # two uploads of one name, one day
    readme = z.read("README.txt").decode()
    assert "old.pdf" in readme and "removed on 2026-09-01" in readme and "2 documents" in readme
    assert not any("old" in n for n in z.namelist() if "/" in n)


def test_a_file_missing_from_storage_is_listed_and_the_rest_still_come(setup):
    s = setup
    topup(s, s["a"].id, 100)
    job_id = _done(s, "user", "doc.pdf")
    f = next(f for f in s["db"].get(Job, uuid.UUID(job_id)).files if f.kind == "report_pdf")
    del get_storage().objects[f.s3_key]
    z = _zip(s["c"]["user"].post(MINE, json={"ids": [job_id]}))
    assert f.name not in _files(z, "doc") and f.name in z.read("README.txt").decode()
    assert any(n.endswith(".pdf") for n in _files(z, "doc"))


def test_the_export_is_audited_and_others_files_count_as_downloads(setup):
    s = setup
    topup(s, s["a"].id, 100)
    own = _done(s, "user", "own.pdf")
    theirs = _done(s, "user2", "theirs.pdf")
    db = s["db"]
    _zip(s["c"]["user"].post(MINE, json={"ids": [own]}))
    ev = db.scalars(select(AuditEvent).where(AuditEvent.action == "documents.exported")).all()
    assert len(ev) == 1 and ev[0].details["documents"] == 1 and ev[0].details["files"] > 1
    assert ev[0].details["others_files"] == 0
    assert not db.scalars(select(AuditEvent).where(AuditEvent.action == "file.accessed")).all()  # their own files
    z = _zip(s["c"]["admin_a"].post(f"{API}/groups/{s['a'].id}/documents/export", json={"ids": [own, theirs]}))
    files = len([n for n in z.namelist() if "/" in n])
    accessed = db.scalars(select(AuditEvent).where(AuditEvent.action == "file.accessed")).all()
    assert len(accessed) == files  # each file, as a download is: what the bulk-download alert counts
    assert {a.details["owner"] for a in accessed} == {"user@a.test", "user2@a.test"}
    assert all(a.details["via"] == "export" for a in accessed)
    group_ev = db.scalars(select(AuditEvent).where(AuditEvent.action == "documents.exported")
                          .order_by(AuditEvent.created_at.desc())).first()
    assert group_ev.details["documents"] == 2 and group_ev.details["list"] == "group documents"


def test_one_export_is_one_request_against_its_own_limit(setup, monkeypatch):
    s = setup
    topup(s, s["a"].id, 100)
    ids = [_done(s, "user", f"d{i}.pdf") for i in range(3)]
    from portal.config import get_portal_settings
    monkeypatch.setattr(get_portal_settings(), "rate_document_exports_per_hour", 2)
    monkeypatch.setattr(get_portal_settings(), "rate_downloads_per_hour", 1)  # three documents' files: still one export
    c = s["c"]["user"]
    assert c.post(MINE, json={"ids": ids}).status_code == 200
    assert c.post(MINE, json={"ids": ids}).status_code == 200
    r = c.post(MINE, json={"ids": ids})
    assert r.status_code == 429 and "zip downloads" in r.json()["detail"]["message"]
    assert c.post(f"{MINE}/plan", json={"ids": ids}).status_code == 200  # seeing what would go in isn't an export


def test_the_zip_is_streamed_a_chunk_at_a_time(setup):
    """Nothing is read ahead: the store is asked for each file only as the zip reaches it, and each piece the response
    gets is about one chunk of a file, however big the export."""
    s = setup
    topup(s, s["a"].id, 100)
    ids = [_done(s, "user", f"d{i}.pdf") for i in range(3)]
    db = s["db"]
    jobs = [db.get(Job, uuid.UUID(i)) for i in ids]
    plan = export.plan(db, jobs, is_super_admin=False, time_zone="UTC", retention_days=30)
    big = 5 * 1024 * 1024
    chunk = 64 * 1024

    class CountingStore:
        def __init__(self):
            self.open = 0
            self.most_open = 0

        def get_stream(self, key):
            self.open += 1
            self.most_open = max(self.most_open, self.open)
            try:
                data = big_file if key == plan.documents[0].entries[0].key else get_storage().objects[key][0]
                for i in range(0, len(data), chunk):
                    yield data[i:i + chunk]
            finally:
                self.open -= 1

    big_file = b"%PDF-" + bytes(big)
    plan.documents[0].entries[0].size = len(big_file)
    store = CountingStore()
    pieces = list(export.stream(plan, store, by="user@a.test"))
    assert store.most_open == 1  # one file at a time
    assert max(len(p) for p in pieces) < 2 * chunk  # never more than about a chunk held
    z = zipfile.ZipFile(io.BytesIO(b"".join(pieces)))
    assert z.read(plan.documents[0].entries[0].name) == big_file and z.testzip() is None


def test_names_in_the_zip_cant_leave_their_folder():
    for hostile in ("../../etc/passwd", "..\\..\\boot.ini", "/abs/path.pdf", "C:\\x.pdf", "a\x00b.pdf", "...", ""):
        name = export.zip_name(hostile)
        assert "/" not in name and "\\" not in name and not name.startswith(".") and "\x00" not in name and name, hostile
    assert export.zip_name("Syllabus: week 1?.pdf") == "Syllabus_ week 1_.pdf"
    assert len(export.zip_name("x" * 300 + ".pdf")) == 100 and export.zip_name("x" * 300 + ".pdf").endswith(".pdf")


def test_one_documents_files_as_a_zip_from_its_page(setup):
    """#507: the document page's "Download everything (zip)": the same files and rules as the list's zip, named from
    its date and file name with underscores, someone else's files recorded with the reason given."""
    s = setup
    topup(s, s["a"].id, 100)
    job_id = _done(s, "user", "course syllabus.pdf")
    page = {f["name"] for f in s["c"]["user"].get(f"{API}/jobs/{job_id}").json()["files"]
            if f["kind"] not in export.STAFF_ONLY_FILES}
    r = s["c"]["user"].get(f"{API}/jobs/{job_id}/files.zip")
    z = _zip(r)
    created = s["db"].get(Job, uuid.UUID(job_id)).created_at
    assert f'filename="{created:%Y-%m-%d}_course_syllabus.zip"' in r.headers["content-disposition"]
    assert _files(z, "course syllabus") == page
    # A group admin: recorded as downloads, with the reason; someone outside the group: not found
    r = s["c"]["admin_a"].get(f"{API}/jobs/{job_id}/files.zip", params={"reason": "checking the tables"})
    files = len([n for n in _zip(r).namelist() if "/" in n])
    db = s["db"]
    accessed = db.scalars(select(AuditEvent).where(AuditEvent.action == "file.accessed")).all()
    assert len(accessed) == files and {a.details["reason"] for a in accessed} == {"checking the tables"}
    ev = db.scalars(select(AuditEvent).where(AuditEvent.action == "documents.exported")
                    .order_by(AuditEvent.created_at.desc())).first()
    assert ev.details["list"] == "document page" and ev.details["documents"] == 1
    assert s["c"]["admin_b"].get(f"{API}/jobs/{job_id}/files.zip").status_code == 404
    assert s["c"]["user2"].get(f"{API}/jobs/{job_id}/files.zip").status_code == 404
    # Files removed: gone, as for a single file
    db.get(Job, uuid.UUID(job_id)).files_deleted_at = datetime.now(timezone.utc)
    db.commit()
    assert s["c"]["user"].get(f"{API}/jobs/{job_id}/files.zip").status_code == 410
