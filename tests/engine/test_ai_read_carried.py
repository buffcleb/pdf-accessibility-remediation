"""A reviewed version keeps the AI's readings flagged: an edit job has no AI enrichment, so the portal sends the
readings flagged on the version it's based on, and they're placed again on the result. One whose tag a person gave
new text in this edit is dropped: they've checked it."""
import json

from app.verification.verify import carried_ai_read
from tests.engine.test_api import client  # noqa: F401 - the fixture


def test_readings_are_carried_unless_the_edit_rewrote_their_tag():
    items = [{"ai_read": [0, [10, 10, 90, 30], "chemistry", "water"], "element": 4},
             {"ai_read": [1, [10, 10, 90, 30], "formula", "x squared"], "element": 7,
              "readings": {"readings": [{"source": "ai", "text": "x squared"}], "differs": {"numbers": {}}}},
             {"ai_read": [2, [0, 0, 5, 5], "formula", "y"], "element": None}]
    ops = [{"op": "set_actual_text", "element": 4, "text": "H two O"}, {"op": "set_role", "element": 7, "role": "P"}]
    got = carried_ai_read(items, ops)
    assert got == [(1, [10, 10, 90, 30], "formula", "x squared", items[1]["readings"]),
                   (2, [0, 0, 5, 5], "formula", "y")]


def test_an_edit_job_flags_the_carried_readings_on_its_result(client, samples):  # noqa: F811
    with (samples / "simple_report.pdf").open("rb") as fh:
        r = client.post("/api/v1/remediate", files={"file": ("report.pdf", fh, "application/pdf")},
                        data={"ai_provider": "mock"})
    body = client.get(f"/api/v1/jobs/{r.json()['job_id']}").json()
    tagmap = client.get(body["reports"]["tag_maps"]["primary"]).json()
    pdf = client.get(body["files"]["remediated_primary"]).content
    paras = [e for e in tagmap["elements"] if e["role"] == "P" and e["own"] and len(e.get("boxes") or []) == 1]
    kept, rewritten = paras[0], paras[1]

    def item(e, spoken):
        box = e["boxes"][0]  # the tag map counts pages from 1, the readings from 0
        return {"ai_read": [box["page"] - 1, box["bbox"], "chemistry", spoken], "element": e["i"]}

    edits = [{"op": "set_actual_text", "element": rewritten["i"], "expect_role": "P", "text": "Written by a person"}]
    r = client.post("/api/v1/edit", files={"file": ("report_remediated.pdf", pdf, "application/pdf")},
                    data={"edits": json.dumps(edits),
                          "ai_read": json.dumps([item(kept, "the AI's reading"), item(rewritten, "replaced")])})
    assert r.status_code == 202, r.text
    edited = client.get(f"/api/v1/jobs/{r.json()['job_id']}").json()
    assert edited["status"] == "completed", edited
    flagged = [f for f in edited["reports"]["tag_verification"]["primary"]["findings"]
               if f["issue"] == "read-aloud text written by AI"]
    assert [f["element"] for f in flagged] == [kept["i"]]
    assert "the AI's reading" in flagged[0]["detail"]
    assert flagged[0]["extra"]["ai_read"] == item(kept, "the AI's reading")["ai_read"]


def test_carried_readings_that_arent_readings_are_refused(client, samples):  # noqa: F811
    with (samples / "simple_report.pdf").open("rb") as fh:
        pdf = fh.read()
    edits = json.dumps([{"op": "set_lang", "element": 0, "lang": "en-US"}])
    for bad in ("not json", json.dumps({"a": 1}), json.dumps([{"ai_read": [0, [1, 2], "x", "y"]}]),
                json.dumps([{"ai_read": [0, [1, 2, 3, 4], "x", "y"], "element": "7"}]),
                json.dumps([{"ai_read": [0, [1, 2, 3, 4], "x", "y" * 2001]}])):
        r = client.post("/api/v1/edit", files={"file": ("x.pdf", pdf, "application/pdf")},
                        data={"edits": edits, "ai_read": bad})
        assert r.status_code == 422, bad
