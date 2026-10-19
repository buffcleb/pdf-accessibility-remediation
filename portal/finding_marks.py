"""Review items signed off (#366): each finding has a key, and a document keeps a status per key (checked, fixed, not
an issue). The key is made from what the finding is about, not where it sits in a list or which tag number it has,
so a status follows the same item into a new version of the document.

The findings list downloads as CSV with each item's status.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from portal.models import FindingMark

STATUS_LABELS = {"checked": "Checked", "fixed": "Fixed", "not_issue": "Not an issue"}
SEVERITY_LABELS = {"high": "High", "review": "Review", "suggest": "Suggestion"}


def key(f: dict) -> str:
    """The same issue, on the same page, about a tag of the same type holding the same text."""
    basis = [f.get("issue") or "", f.get("page"), f.get("role") or "", (f.get("text") or "")[:120]]
    return hashlib.sha1(json.dumps(basis, ensure_ascii=False).encode()).hexdigest()[:20]


def with_keys(findings: list | None) -> list:
    """The findings, each with its key (a copy: the stored summary isn't changed)."""
    return [{**f, "key": key(f)} if isinstance(f, dict) else f for f in (findings or [])]


def summary_with_keys(summary: dict | None, *path: str) -> dict | None:
    """A stored result or version summary with keys on the findings at path (("review", "findings") or
    ("findings",))."""
    if not isinstance(summary, dict):
        return summary
    out = dict(summary)
    node = out
    for part in path[:-1]:
        if not isinstance(node.get(part), dict):
            return out
        node[part] = dict(node[part])
        node = node[part]
    if isinstance(node.get(path[-1]), list):
        node[path[-1]] = with_keys(node[path[-1]])
    return out


def marks_for(db: Session, job_id) -> dict[str, FindingMark]:
    return {m.key: m for m in db.scalars(select(FindingMark).where(FindingMark.job_id == job_id))}


def _cell(v) -> str:
    """A spreadsheet reads a cell starting with =, +, - or @ as a formula: text that does is kept as text."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


def findings_csv(findings: list, marks: dict[str, FindingMark], total: int) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Severity", "Issue", "Page", "Tag", "Text", "Detail", "Suggested action", "Status", "Note",
                "Status set by", "Status set at"])
    for f in findings:
        m = marks.get(key(f))
        w.writerow([_cell(x) for x in (
            SEVERITY_LABELS.get(f.get("severity"), f.get("severity")), f.get("issue"), f.get("page"), f.get("role"),
            f.get("text"), f.get("detail"), f.get("suggested_alt") and f"Suggested alt text: {f['suggested_alt']}"
            or f.get("action"), STATUS_LABELS.get(m.status) if m else "", m.note if m else "",
            m.set_by_email if m else "", m.set_at.isoformat(timespec="minutes") if m else "")])
    if total > len(findings):
        w.writerow([])
        w.writerow([f"The first {len(findings)} of {total} items; the full list is in the report."])
    return buf.getvalue()
