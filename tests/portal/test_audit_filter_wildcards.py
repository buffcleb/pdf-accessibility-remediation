"""The audit filters take a prefix of the event name. "%" and "_" in it are characters, not LIKE wildcards: a
filter of "%." used to match every event with a dot in its name."""
from tests.portal.test_admin import API, world  # noqa: F401 - the fixture
from tests.portal.test_jobs import fake_engine, setup  # noqa: F401 - fixtures the report data needs
from tests.portal.test_live_reports import _get, data  # noqa: F401 - the fixture


def test_the_group_audit_prefix_takes_wildcards_as_characters(world):
    admin = world["c"]["admin_a"]
    assert admin.get(f"{API}/groups/{world['a'].id}/audit").json()  # there are events to over-match
    assert admin.get(f"{API}/groups/{world['a'].id}/audit", params={"action": "%."}).json() == []
    assert admin.get(f"{API}/groups/{world['a'].id}/audit", params={"action": "_ember."}).json() == []


def test_the_audit_report_event_filter_takes_wildcards_as_characters(data):
    assert _get(data, "super", "audit")["rows"]
    assert _get(data, "super", "audit", event="_")["rows"] == []
    assert _get(data, "super", "audit", event="%")["rows"] == []
