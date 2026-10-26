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

from app.pipeline.outputs import add_bookmarks, move_xml, snapshot, tagmap_file, validate, visual_fidelity
from app.pipeline.run_result import RunResult
from app.storage import JobStore
from app.tagging.editor import apply_edits
from app.validation.audit import audit_pdf
from app.verification.integrity import check_integrity
from app.verification.model import IntegrityError
from app.verification.verify import VerifyOptions, carried_ai_read, verify_output


def _elements_with_alt(path, alts: set[str]) -> set[int]:
    with pikepdf.open(path) as pdf:
        _, index = check_integrity(pdf)
    return {e.index for e in index.elements if str(e.obj.get("/Alt", "") or "").strip() in alts}


def reading_choices(ops: list[dict]) -> list[dict]:
    """The choices people made between a formula's two readings (#403), from the edits that carry one: which formula,
    which reading ("ai", "rule", "picture": the formula reader's, or "own": their own words), and the text the tag
    now has. For improving the engine:
    which reading turns out right, on which kinds of formula."""
    out = []
    for op in ops:
        r = op.get("reading")
        if not isinstance(r, dict) or op.get("op") not in ("set_alt", "set_actual_text"):
            continue
        readings = r.get("readings") if isinstance(r.get("readings"), dict) else {}
        out.append({"formula": str(r.get("formula") or "")[:80], "element": op.get("element"),
                    "chose": r.get("chose") if r.get("chose") in ("ai", "rule", "picture", "own") else "own",
                    "readings": {k: str(readings.get(k) or "")[:2000] for k in ("ai", "rule", "picture")
                                 if k in readings or k != "picture"},
                    "text": str(op.get("alt") if op["op"] == "set_alt" else op.get("text") or "")[:2000]})
    return out


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
    # The AI's readings the version it's based on flagged stay flagged, unless a person wrote that tag's text now
    opts.ai_read = carried_ai_read(meta.get("ai_read") or [], ops)
    rep = await verify_output(dst, opts)
    if not rep.integrity_passed:
        raise IntegrityError("reviewed", rep.errors)
    # Edits can change the headings: bookmarks the engine made follow them, and a file with none gets them. A
    # document's own working bookmarks are never touched, and a file checked without edits stays as it is
    bookmarks = (await add_bookmarks(dst, rep.tagmap))[0] if ops else None

    # The file and its tag map are final now: published before the checks that follow, so the version can be opened
    # while veraPDF and the pixel comparison run (#428). The tag tree passed its integrity check above
    tagmap = await tagmap_file(out, stem, "primary", dst, rep.tagmap)
    store.write_partial({"files": {"remediated_primary": dst.name}, "tag_map": tagmap})

    progress("validating", 70)
    vera = await asyncio.to_thread(validate, dst, work, "after", stem)
    snap = snapshot(await asyncio.to_thread(audit_pdf, dst), vera)
    # A corrected file is compared with the version it replaces; an edit with the file it started from
    reference = work / "reference.pdf" if meta.get("reference") else None
    compared = reference if reference and reference.exists() else None
    fidelity = await asyncio.to_thread(visual_fidelity, compared or store.input_pdf, dst, edited=compared is None)

    result = RunResult(
        files={"remediated_primary": dst.name, "remediated_visual_fallback": None},
        metrics={"pages_processed": snap.get("pages"), "edits_applied": len(log),
                 "processing_time_seconds": round(time.monotonic() - started, 2)},
        reports={
            "verapdf_before": None,
            "verapdf_after": {"primary": move_xml(vera, out, f"{stem}_verapdf_after")},
            "comparison": {"before": None, "after": snap, "visual_fidelity": fidelity, "bookmarks": bookmarks},
            "remediation_summary": (f"Applied {len(log)} tag edit(s)." if ops else
                                    "Checked a corrected file." if reference else "Re-reviewed without changes.")
            + (" AI review: flagged items and a sample." if review == "sampled" else
               " AI review: every figure." if review == "full" else ""),
            "ai_review": review,
            "edits_applied": log,
            # Formulas whose two readings disagreed, and which one a person chose (#403)
            "math_choices": reading_choices(ops) or None,
            "tag_verification": {"primary": rep.to_dict()},
            "tag_maps": {"primary": tagmap},
        },
        warnings=[] if fidelity.get("identical") else
        [f"Pages {fidelity.get('differing_pages')} render differently "
         + ("from the version it replaces." if reference else "after the edits.")],
    ).to_dict()
    progress("completed", 100)
    return result
