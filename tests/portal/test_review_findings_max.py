"""How many review items are kept with a document is a setting. It was 300, built in: with the engine's reviewer
set to look at more, the rest of what it found had nowhere to show in the app."""
from portal import worker
from portal.config import get_portal_settings


def _resp(n):
    findings = [{"severity": "suggest" if i % 2 else "review", "page": i, "issue": f"item {i}"} for i in range(n)]
    return {"reports": {"tag_verification": {"primary": {"findings": findings}}}}


def test_the_default_keeps_300_and_says_how_many_there_were():
    for summary in (worker.summarize(_resp(450))["review"], worker.version_summary(_resp(450), "primary")):
        assert len(summary["findings"]) == 300 and summary["findings_total"] == 450
        assert summary["findings"][0]["severity"] == "review"   # the most serious first, so those are the ones kept


def test_a_higher_setting_keeps_more(monkeypatch):
    monkeypatch.setattr(get_portal_settings(), "review_findings_max", 1000)
    assert len(worker.summarize(_resp(450))["review"]["findings"]) == 450
    assert len(worker.version_summary(_resp(1200), "primary")["findings"]) == 1000
