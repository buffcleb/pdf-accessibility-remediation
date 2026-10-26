"""What every kind of job (remediation, Adobe, tag edits) writes and measures the same way: the validator's verdicts
and their XML, the snapshot of a file's checks, visual fidelity, bookmarks, tag maps and the JSON, Markdown and PDF
reports; and the clock of where a job's time goes (refactor plan item 7). The orchestrator, adobe_job and edit_job
import these from here.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import shutil
import time
from pathlib import Path
from typing import Callable

import pymupdf as fitz

from app.pipeline.analyzer import capped_dpi
from app.storage import JobStore
from app.tagging.bookmarks import write_bookmarks
from app.validation.verapdf import VeraResult, run_verapdf
from app.validation.verapdf import flavours as verapdf_flavours
from app.validation.verapdf import pdfua_part
from app.verification.tagmap import build_tagmap

log = logging.getLogger(__name__)

Progress = Callable[[str, int], None]


class StageClock:
    """Where a document's time goes: seconds per stage (a stage runs from one progress mark to the next; one
    visited twice adds up), and within them the time spent waiting for the validator."""

    def __init__(self, progress: Progress):
        self._progress = progress
        self._stage: str | None = None
        self._since = time.monotonic()
        self.stages: dict[str, float] = {}
        self.parts: dict[str, float] = {}

    def __call__(self, stage: str, pct: int) -> None:
        self._close()
        self._stage = stage
        self._progress(stage, pct)

    def _close(self) -> None:
        now = time.monotonic()
        if self._stage is not None:
            self.stages[self._stage] = self.stages.get(self._stage, 0.0) + now - self._since
        self._since = now

    def add(self, part: str, seconds: float) -> None:
        self.parts[part] = self.parts.get(part, 0.0) + seconds

    def seconds(self) -> dict:
        """So far, in the order the stages were first reached; the stage in progress counts up to now."""
        self._close()
        out = {k: round(v, 1) for k, v in self.stages.items() if k != "completed"}
        if self.parts:
            out["of_which"] = {k: round(v, 1) for k, v in self.parts.items()}
        return out


# The clock of the document being processed (threads started with asyncio.to_thread see the same one)
current_clock: contextvars.ContextVar[StageClock | None] = contextvars.ContextVar("stage_clock", default=None)


def validate(pdf: Path, out_dir: Path, label: str, stem: str) -> dict[str, VeraResult]:
    """Each profile (PDF/UA-1, and PDF/UA-2 for information) checked, at the same time when the veraPDF service is
    there (it runs several copies; each check waits on it, not on this process): about a minute and a half less on a
    500-page document, which is checked six to eight times."""
    import contextvars
    from concurrent.futures import ThreadPoolExecutor

    from app.validation.verapdf import uses_service

    flavours = verapdf_flavours(pdfua_part(pdf))  # the verdict by the part the file can conform to (#409)
    started = time.monotonic()

    def one(fl: str) -> VeraResult:
        return run_verapdf(pdf, out_dir / f"{stem}_verapdf_{label}_{fl}.xml", fl)
    if len(flavours) > 1 and uses_service():
        # Each in a copy of this job's context: its validator choices (job_validator) live there
        with ThreadPoolExecutor(max_workers=len(flavours)) as pool:
            futures = [pool.submit(contextvars.copy_context().run, one, fl) for fl in flavours]
            results = {fl: f.result() for fl, f in zip(flavours, futures)}
    else:
        results = {fl: one(fl) for fl in flavours}
    if (clock := current_clock.get()) is not None:
        clock.add("validator", time.monotonic() - started)
    return results


def snapshot(audit: dict, vera: dict[str, VeraResult]) -> dict:
    primary = next(iter(vera.values()), None)
    snap = {
        # Which PDF/UA the verdict is by: 2 for a PDF 2.0 file, else 1 (#409)
        "pdfua_part": 2 if next(iter(vera), None) == "ua2" else 1,
        "compliant": primary.compliant if primary and primary.available else None,
        "failed_rules": primary.failed_rules if primary else None,
        "failed_checks": primary.failed_checks if primary else None,
        "verapdf": {
            fl: ({"compliant": v.compliant, "passed_rules": v.passed_rules, "failed_rules": v.failed_rules,
                  "failed_checks": v.failed_checks, "profile": v.profile,
                  "failed_clauses": sorted(v.failed_clauses),
                  "failures": [{"clause": f.clause, "test": f.test, "description": f.description,
                                "failed_checks": f.failed_checks, "pages": [p + 1 for p in f.pages][:20]}
                               for f in v.failures[:25]]} if v.available and not v.error
                 else {"error": v.error or "unavailable"})
            for fl, v in vera.items()
        },
    }
    snap.update(audit)
    return snap


def drawing(path: Path) -> list[str]:
    """Each page's drawing instructions, as a fingerprint: its content streams and the Form XObjects it draws (and the
    forms they draw), byte for byte. Two pages with the same fingerprint, in a file and the file the tag editor made
    from it (which changes only content streams and the tag tree), draw the same."""
    import hashlib

    import pikepdf

    out = []
    with pikepdf.open(path) as pdf:
        for page in pdf.pages:
            h = hashlib.sha1()
            try:
                h.update(page.obj.Contents.read_bytes() if isinstance(page.obj.get("/Contents"), pikepdf.Stream)
                         else b"".join(c.read_bytes() for c in page.obj.get("/Contents") or []))
                seen, todo = set(), [page.obj.get("/Resources")]
                while todo:
                    res = todo.pop()
                    xo = res.get("/XObject") if isinstance(res, pikepdf.Dictionary) else None
                    for name in sorted(xo.keys()) if isinstance(xo, pikepdf.Dictionary) else []:
                        x = xo[name]
                        if not isinstance(x, pikepdf.Stream) or x.get("/Subtype") != "/Form" or x.objgen in seen:
                            continue
                        seen.add(x.objgen)
                        h.update(name.encode() + x.read_raw_bytes())
                        todo.append(x.get("/Resources"))
                out.append(h.hexdigest())
            except (pikepdf.PdfError, AttributeError, TypeError, ValueError):
                out.append(f"unreadable-{len(out)}")  # never the same as another file's: it's drawn and compared
    return out


def visual_fidelity(a: Path, b: Path, dpi: int = 50, edited: bool = False) -> dict:
    """Whether every page of b draws the same as a's, compared at a low resolution. edited: b is the tag editor's
    output of a, so a page whose drawing instructions are unchanged draws the same and isn't drawn to compare (#428)."""
    same = []
    if edited:
        fa, fb = drawing(a), drawing(b)
        same = [len(fa) == len(fb) and x == y for x, y in zip(fa, fb)]
    da, db = fitz.open(a), fitz.open(b)
    try:
        differing = []
        for i in range(min(da.page_count, db.page_count)):
            if i < len(same) and same[i]:
                continue
            d = capped_dpi(da[i], dpi)  # the same for both: the pages are the same size
            if da[i].get_pixmap(dpi=d, alpha=False).samples != db[i].get_pixmap(dpi=d, alpha=False).samples:
                differing.append(i + 1)
        return {"pages_compared": da.page_count, "pages_identical": da.page_count - len(differing),
                "differing_pages": differing[:50], "identical": not differing and da.page_count == db.page_count}
    finally:
        da.close()
        db.close()


def move_xml(vera: dict[str, VeraResult] | None, out: Path, prefix: str) -> str | None:
    if not vera:
        return None
    primary_name = None
    for i, (fl, r) in enumerate(vera.items()):
        if r.xml_path and Path(r.xml_path).exists():
            name = f"{prefix}_{fl}.xml"
            shutil.copyfile(r.xml_path, out / name)
            if i == 0:
                primary_name = name
    return primary_name


def xml_name(vera: dict[str, VeraResult]) -> str | None:
    r = next(iter(vera.values()), None)
    return Path(r.xml_path).name if r and r.xml_path else None


def delta(before: dict, after: dict) -> dict:
    keys = ["failed_rules", "failed_checks", "struct_elements", "figures_missing_alt", "tables_without_headers",
            "heading_level_skips", "content_tagged_ratio", "annotations_untagged", "font_problems"]
    out = {}
    for k in keys:
        b, a = before.get(k), after.get(k)
        if isinstance(b, (int, float)) and isinstance(a, (int, float)):
            out[k] = {"before": b, "after": a, "change": round(a - b, 4)}
    return out


def verdict_line(label: str, snap: dict) -> str:
    if snap.get("compliant") is None:
        return f"{label}: veraPDF verdict unavailable."
    status = "PASSES" if snap["compliant"] else "FAILS"
    return f"{label}: {status} PDF/UA validation ({snap['failed_rules']} failed rules, {snap['failed_checks']} failed checks)."


async def add_bookmarks(path: Path, tagmap: dict | None) -> tuple[dict, dict | None]:
    """Write bookmarks from the headings into an output (see app/tagging/bookmarks.py). Returns what was done and the
    tag map, built here when the caller had none. Bookmarks are a help, not a requirement: a failure is logged."""
    try:
        if tagmap is None:
            tagmap = await asyncio.to_thread(build_tagmap, path)
        return await asyncio.to_thread(write_bookmarks, path, tagmap), tagmap
    except Exception as exc:  # noqa: BLE001
        log.warning("bookmarks for %s failed: %s", path.name, exc)
        return {"bookmarks": 0, "outline": "failed"}, tagmap


async def tagmap_file(out: Path, stem: str, label: str, pdf_path: Path, data: dict | None = None) -> str | None:
    """Write the viewer's tag map for one file; returns its name, or None if it couldn't be built."""
    try:
        if data is None:
            data = await asyncio.to_thread(build_tagmap, pdf_path)
    except Exception as exc:  # noqa: BLE001 - the viewer is optional; never fail a job over it
        log.warning("tag map for %s failed: %s", label, exc)
        return None
    name = f"{stem}_tagmap_{label}.json"
    (out / name).write_text(json.dumps(data, separators=(",", ":")))
    return name


def write_json_report(out: Path, stem: str, result: dict, store: JobStore | None = None) -> None:
    from app.reports.markdown import render_job_markdown

    name, md_name = f"{stem}_remediation_report.json", f"{stem}_remediation_report.md"
    result["full_report"] = name
    result["markdown_report"] = md_name
    (out / name).write_text(json.dumps(result, indent=2, default=str))
    meta = (store.read_meta() if store else None) or {"filename": f"{stem}.pdf"}
    # Links in the file are sibling file names, so they resolve when the outputs are downloaded together.
    (out / md_name).write_text(render_job_markdown(store.job_id if store else "", meta, "completed", result))
    # The same report for people: an accessible (PDF/UA-1) PDF, checked like any other output
    from app.reports.pdf import write_report_pdf
    pdf_name = f"{stem}_remediation_report.pdf"
    check = write_report_pdf(out / pdf_name, meta, result, (meta.get("flags") or {}).get("report_branding"))
    if (out / pdf_name).exists():
        result["report_pdf"] = pdf_name
        result["report_pdf_compliant"] = check.get("compliant")
        (out / name).write_text(json.dumps(result, indent=2, default=str))
