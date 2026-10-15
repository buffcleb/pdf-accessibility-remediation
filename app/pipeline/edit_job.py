"""Edit jobs: apply people's tag edits to a remediated file, then validate it like any output.

The result has the same shape as a remediation result, so the API's job and file routes (and the
portal's poller) handle it unchanged.
"""
from __future__ import annotations

import asyncio
import shutil
import time
from collections.abc import Callable

import pikepdf

from app.pipeline.orchestrator import _bookmarks, _move_xml, _snapshot, _tagmap_file, _validate, _visual_fidelity
from app.storage import JobStore
from app.tagging.editor import apply_edits
from app.validation.audit import audit_pdf
from app.verification.integrity import check_integrity
from app.verification.model import IntegrityError
from app.verification.verify import VerifyOptions, verify_output


def _elements_with_alt(path, alts: set[str]) -> set[int]:
    with pikepdf.open(path) as pdf:
        _, index = check_integrity(pdf)
    return {e.index for e in index.elements if str(e.obj.get("/Alt", "") or "").strip() in alts}


async def run_edit(store: JobStore, progress: Callable[[str, int], None]) -> dict:
    meta = store.read_meta() or {}
    started = time.monotonic()
    stem = meta.get("stem") or "document"
    out, work = store.output_dir, store.work_dir
    ops = meta.get("edits") or []

    review = meta.get("review") or "none"
    progress("applying_edits", 10)
    dst = out / f"{stem}_reviewed.pdf"
    if ops:
        log = await asyncio.to_thread(apply_edits, store.input_pdf, dst, ops)
    else:  # review only: the file as it is
        await asyncio.to_thread(shutil.copyfile, store.input_pdf, dst)
        log = []

    progress("verifying_tags", 40)
    if review in ("sampled", "full"):
        # The same AI review a new remediation gets: everything flagged plus a sample of the rest, or every figure
        opts = VerifyOptions(ai=bool(meta.get("verify_ai", True)), seed=store.job_id, full_review=review == "full",
                             judge_model=meta.get("judge_model") or None)
    else:
        # The judge re-checks only the alt text people wrote.
        new_alts = {op["alt"].strip() for op in ops if op.get("op") == "set_alt" and op.get("alt", "").strip()}
        edited = await asyncio.to_thread(_elements_with_alt, dst, new_alts) if new_alts else set()
        opts = VerifyOptions(ai=bool(meta.get("verify_ai", True)) and bool(edited), seed=store.job_id,
                             only_elements=edited, judge_model=meta.get("judge_model") or None)
    rep = await verify_output(dst, opts)
    if not rep.integrity_passed:
        raise IntegrityError("reviewed", rep.errors)
    # Edits can change the headings: bookmarks the engine made follow them, and a file with none gets them. A
    # document's own working bookmarks are never touched, and a file checked without edits stays as it is
    bookmarks = (await _bookmarks(dst, rep.tagmap))[0] if ops else None

    progress("validating", 70)
    vera = await asyncio.to_thread(_validate, dst, work, "after", stem)
    snapshot = _snapshot(await asyncio.to_thread(audit_pdf, dst), vera)
    tagmap = await _tagmap_file(out, stem, "primary", dst, rep.tagmap)
    # A corrected file is compared with the version it replaces; an edit with the file it started from
    reference = work / "reference.pdf" if meta.get("reference") else None
    fidelity = await asyncio.to_thread(_visual_fidelity, reference if reference and reference.exists() else store.input_pdf, dst)

    result = {
        "files": {"remediated_primary": dst.name, "remediated_visual_fallback": None},
        "metrics": {"pages_processed": snapshot.get("pages"), "edits_applied": len(log),
                    "processing_time_seconds": round(time.monotonic() - started, 2)},
        "reports": {
            "verapdf_before": None,
            "verapdf_after": {"primary": _move_xml(vera, out, f"{stem}_verapdf_after")},
            "comparison": {"before": None, "after": snapshot, "visual_fidelity": fidelity, "bookmarks": bookmarks},
            "remediation_summary": (f"Applied {len(log)} tag edit(s)." if ops else
                                    "Checked a corrected file." if reference else "Re-reviewed without changes.")
            + (" AI review: flagged items and a sample." if review == "sampled" else
               " AI review: every figure." if review == "full" else ""),
            "ai_review": review,
            "edits_applied": log,
            "tag_verification": {"primary": rep.to_dict()},
            "tag_maps": {"primary": tagmap},
        },
        "warnings": [] if fidelity.get("identical") else
        [f"Pages {fidelity.get('differing_pages')} render differently "
         + ("from the version it replaces." if reference else "after the edits.")],
    }
    progress("completed", 100)
    return result
