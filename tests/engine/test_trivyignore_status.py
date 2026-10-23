"""scripts/trivyignore_status.py: what .trivyignore hides, for build.sh to list after each build, with a reminder as
each ignore's end (a fix's two-week wait) comes near and once it has passed."""
import importlib.util
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("trivyignore_status", ROOT / "scripts" / "trivyignore_status.py")
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)

SAMPLE = """# Vulnerabilities scripts/build.sh's Trivy scan should not report (#362)

# 2026-10-21, pillow in the formula image: decoded crops are PNG only
CVE-1 exp:2026-11-03
CVE-2 exp:2026-11-03
CVE-3 exp:2026-11-03
CVE-4 exp:2026-11-03

# 2026-10-22, certifi: no HTTPS calls while running
GHSA-1 exp:2026-10-30

# Looked at long ago: not reachable
CVE-9
"""


def test_grouped_by_reason_and_end_date():
    got = {(g["reason"][:16], g["exp"]): g["ids"] for g in ts.groups(SAMPLE)}
    assert got == {("2026-10-21, pill", "2026-11-03"): ["CVE-1", "CVE-2", "CVE-3", "CVE-4"],
                   ("2026-10-22, cert", "2026-10-30"): ["GHSA-1"], ("Looked at long a", None): ["CVE-9"]}


def test_days_left_then_due_then_expired_most_urgent_first():
    early = ts.status(SAMPLE, date(2026, 10, 22))
    assert early[0] == "ignored findings (.trivyignore): 6"
    assert early[1].startswith("  until 2026-10-30 (8 days): GHSA-1 (2026-10-22, certifi")
    assert early[2].startswith("  until 2026-11-03 (12 days): CVE-1, CVE-2, CVE-3 and 1 more (2026-10-21, pillow")
    assert early[3].startswith("  no end date, revisit: CVE-9")
    later = ts.status(SAMPLE, date(2026, 11, 1))
    assert later[1].startswith("  EXPIRED 2026-10-30: Trivy reports them again, bump the fix now: GHSA-1")
    assert later[2].startswith("  DUE 2026-11-03, in 2 days: the two-week wait is nearly over, bump the fix then: CVE-1")


def test_nothing_ignored_prints_nothing(tmp_path):
    assert ts.status("# only comments\n\n", date(2026, 10, 22)) == []
    assert ts.main(["x", str(tmp_path / "missing")]) == 0


def test_the_repositorys_file_reads():
    """Every line of the real .trivyignore parses, with a date where it has one."""
    for g in ts.groups((ROOT / ".trivyignore").read_text()):
        assert g["ids"] and (g["exp"] is None or date.fromisoformat(g["exp"]))
