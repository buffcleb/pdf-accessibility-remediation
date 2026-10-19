"""The limits a document reached (#312), gathered into one list for its metrics: what the AI steps recorded as they
cut something short (EnrichmentStats.limit_hit), what the tag review left out, and the findings list being shortened.
Each: {"setting", "value", "wanted", "effect"}. A super admin reads them to tell whether a limit needs raising."""
from __future__ import annotations

import re

from app.config import get_settings

FINDINGS_KEPT = 100  # findings kept in a job's result and report


def from_verification(verification: dict | None, pages: int | None) -> list[dict]:
    """The tag review's limits, across the outputs it checked (the largest want of each)."""
    s = get_settings()
    out: dict[str, dict] = {}
    for rep in (verification or {}).values():
        if not isinstance(rep, dict):
            continue
        ai = rep.get("ai") or {}
        if ai.get("capped_at"):
            wanted = int(ai.get("figures_selected") or 0) + int(ai.get("pages_selected") or 0)
            x = out.setdefault("VERIFY_AI_MAX_ITEMS", {"setting": "VERIFY_AI_MAX_ITEMS", "value": int(ai["capped_at"]),
                                                       "wanted": 0, "effect": "items weren't reviewed by the AI"})
            x["wanted"] = max(x["wanted"], wanted)
        if any(re.search(r"limited to the first \d+ pages", m) for m in rep.get("skipped") or []):
            out.setdefault("VERIFY_MAX_PAGES", {"setting": "VERIFY_MAX_PAGES", "value": s.verify_max_pages,
                                                "wanted": int(pages or 0),
                                                "effect": "pages past it weren't checked for tag meaning"})
    return list(out.values())


def collect(recorded: list[dict], verification: dict | None, findings: list, pages: int | None) -> list[dict]:
    """Everything the document reached: recorded by the AI steps, from the tag review, and the findings list."""
    out = [dict(x) for x in recorded] + from_verification(verification, pages)
    if len(findings) > FINDINGS_KEPT:
        out.append({"setting": "FINDINGS_KEPT", "value": FINDINGS_KEPT, "wanted": len(findings),
                    "effect": "findings past it aren't in the report (the counts by reason still are)"})
    return out


def describe(x: dict, with_setting: bool = True) -> str:
    """One limit reached, for a reader: "40 regions of specialist notation kept the layout rules' reading
    (AI_NOTATION_MAX, set to 100)". A per-page limit counts its pages."""
    n = x.get("wanted", 0) if x.get("count") else max(0, int(x.get("wanted", 0)) - int(x.get("value", 0)))
    text = f"{n:,} {x.get('effect', '')}".strip()
    return f"{text} ({x.get('setting')}, set to {x.get('value'):,})" if with_setting else text

