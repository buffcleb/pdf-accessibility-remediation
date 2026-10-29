"""End-to-end remediation flow for one job.

  1. Validate the ORIGINAL (veraPDF + internal audit)        -> "before" report
  2. Fix font Unicode maps and check marks (rendering unchanged); analyze layout (PyMuPDF);
     OCR scanned pages, the AI correcting unsure words (invisible text layer); figures on scans;
     music examples as figures, read by the music reading service
  3. AI enrichment: alt text, table structure, reading order, notation the rules can't read
  4. Non-destructive tagging (existing valid tags retained)  -> visual output
  5. Validate; glyph widths and glyph lists fixed if pixel-identical. If failures remain that only
     destructive fixes can address: substitute fonts, then flatten the affected pages and tag again
                                                             -> modified output
  6. Verify every output (integrity, semantics, AI review), tag maps for the viewer
                                                             -> "after" report, comparison, summary, reports
  docs/architecture/engine-pipeline.md says the same, step by step, with the rules each one follows.
"""
from __future__ import annotations

import asyncio
import re
import json
import logging
import shutil
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import pikepdf
import pymupdf as fitz

from app.ai.base import AIError
from app.ai.registry import get_provider
from app.config import get_settings
from app.pipeline.analyzer import analyze_document, covers_page, line_shaped
from app.pipeline.enrichment import EnrichmentStats, enrich, render_slots
from app.pipeline.models import DocumentAnalysis, bbox_area, bbox_intersection
from app.pipeline.outputs import (Progress, StageClock, add_bookmarks, current_clock, delta, move_xml, snapshot, tagmap_file,
                                  validate, verdict_line, visual_fidelity, write_json_report, xml_name)
from app.pipeline.run_result import RunResult, ai_usage, timed
from app.pipeline.ocr import block_confidence, ocr_lines, tesseract_available, tesseract_cli, write_ocr_layer
from app.pipeline.ocr_ai import correct_lines
from app.pipeline import field_labels, limits, mathml, music, protection, scan_figures
from app.tagging import mathml_embed
from app.pipeline.language import document_language
from app.pipeline.structure import assign_roles, detect_columns, heuristic_reading_order
from app.pipeline.transfer import transfer_enrichment
from app.storage import JobStore
from app.tagging.destructive import DESTRUCTIVE_CLAUSES, flatten_pages
from app.tagging.font_embed import embed_standard_fonts
from app.tagging.font_unicode import add_missing_tounicode
from app.tagging.form_marks import redraw_dingbat_marks
from app.tagging.font_metrics import fix_width_mismatches
from app.tagging.tagger import TagResult, apply_written_alts, tag_document, write_figure_alts
from app.validation.audit import audit_pdf
from app.validation.verapdf import VeraResult
from app.verification.model import IntegrityError
from app.verification.tagmap import build_tagmap
from app.verification.verify import VerifyOptions, verify_output

log = logging.getLogger(__name__)



class AIProviderUnavailable(RuntimeError):
    """The requested AI provider cannot be reached or lacks the model: fail the job up front."""


@dataclass
class JobOptions:
    stem: str
    provider: str = "none"
    model: str | None = None
    debug: bool = False
    report_only: bool = False
    return_base64: bool = False
    language: str | None = None
    title: str | None = None
    full_review: bool = False
    verify_ai: bool = True
    job_id: str = "0"
    ai_tier: str | None = None  # the portal's AI tier, for attributing AI spend
    judge_model: str | None = None  # the tier's review model at the gateway
    route: str = "standard"  # standard | adobe
    notation_ai: bool = True  # the AI reads specialist notation the layout rules can't
    ocr_ai: bool = True  # the AI corrects OCR words Tesseract was unsure of
    ignore_existing: bool = False  # the document's own tags are poor: tag it afresh from the page (#396)
    pdfua: str = "1"  # "1", "2" (delivered as PDF 2.0, PDF/UA-2) or "both" (a PDF/UA-2 copy too) (#445)

    @classmethod
    def from_meta(cls, meta: dict) -> "JobOptions":
        flags = meta.get("flags") or {}
        return cls(
            stem=meta.get("stem") or "document",
            provider=meta.get("ai_provider") or "none",
            model=meta.get("ai_model") or None,
            debug=bool(flags.get("debug")),
            report_only=bool(flags.get("report_only")),
            return_base64=bool(flags.get("return_base64")),
            language=flags.get("language"),
            title=flags.get("title"),
            full_review=bool(flags.get("full_review")),
            verify_ai=flags.get("verify_ai", True) is not False,
            job_id=meta.get("job_id") or "0",
            ai_tier=meta.get("ai_tier") or None,
            judge_model=meta.get("judge_model") or None,
            route=meta.get("route") or "standard",
            notation_ai=flags.get("notation_ai", True) is not False,
            ocr_ai=flags.get("ocr_ai", True) is not False,
            ignore_existing=bool(flags.get("ignore_existing")),
            pdfua=str(flags.get("pdfua") or "1"),
        )


# --------------------------------------------------------------------------- helpers
def prepare_analysis(path: Path, ocr_pages=(), ocr_records=()) -> DocumentAnalysis:
    """ocr_pages: the pages given the engine's OCR layer; ocr_records: its lines, when known, for how sure the OCR
    was of each block's words (a scanned formula reads with low confidence, as does a line it mostly failed on)."""
    analysis = analyze_document(path, ocr_pages)
    if ocr_records:
        block_confidence([p for p in analysis.pages if p.number in ocr_pages], ocr_records)
    assign_roles(analysis)
    for p in analysis.pages:
        p.columns = detect_columns(p)
        p.reading_order = heuristic_reading_order(p)
    return analysis


TITLE_MAX = 1000  # a title from the file's own metadata is its producer's to write; past this it isn't a title

# A language tag as PDF/UA wants it (BCP 47: "en", "en-US", "zh-Hant-TW"); the file's own /Lang is its producer's to
# write, and one that isn't a tag would be copied into the output as it is
_LANGUAGE_TAG = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{1,8})*$")


# What a producer writes when the author gave no title: the application and the file's name ("Microsoft Word -
# syllabus.docx", "Microsoft PowerPoint - Lecture 3.pptx"), or the name alone. A reader announces it as the title.
_APP_PREFIX = re.compile(r"^microsoft\s+(?:office\s+)?(?:word|powerpoint|excel|publisher|visio|outlook)(?:\s*-.*)?$",
                         re.I | re.S)
_FILE_NAME = re.compile(r"^[^\n]{0,250}\.(?:docx?|docm|dotx?|pptx?|ppsx?|xlsx?|xlsm|rtf|odt|odp|ods|pages|key|"
                        r"numbers|pdf|txt|html?|tex|indd|pub|vsdx?|ps|eps|qxp)$", re.I)
# A source file's name with no extension, as TeX front ends write it ("ps1", "hw_03", "lec-7b"): one short token with
# a digit, underscore or hyphen in it
_STEM = re.compile(r"^(?=[^\s]*[\d_-])[^\s]{1,16}$")
_NO_TITLE = {"untitled", "untitled document", "document", "no title", "title", "powerpoint presentation"}


def usable_title(title: str | None) -> bool:
    """Whether a title from the file's own metadata says what the document is: not empty, not a placeholder, not an
    application's name with the file's name ("Microsoft Word - x.docx"), not a file name."""
    t = (title or "").strip()
    return bool(t) and t.lower() not in _NO_TITLE and not _APP_PREFIX.match(t) and not _FILE_NAME.match(t)


def _stem_title(title: str, analysis: DocumentAnalysis) -> bool:
    """Whether a title is the name of the file it was made from ("ps1" for a problem set): a short token like a
    file's name that the first page doesn't print. One it prints ("W-9", "1040") is the document's name."""
    t = title.strip()
    if not _STEM.match(t):
        return False
    first = " ".join(b.text for b in analysis.pages[0].blocks).lower() if analysis.pages else ""
    return t.lower() not in first


def _pick_title(opts: JobOptions, analysis: DocumentAnalysis) -> str:
    if opts.title:
        return opts.title
    if usable_title(analysis.title) and not _stem_title(analysis.title, analysis):
        return analysis.title.strip()[:TITLE_MAX]
    for p in analysis.pages:
        for b in p.blocks:
            if b.role == "H1":
                return b.text[:200]
    return opts.stem.replace("_", " ")


def _pick_language(opts: JobOptions, analysis: DocumentAnalysis, default: str) -> tuple[str, str | None]:
    """The job's language; else the file's own when it is a well-formed tag and the text doesn't clearly say
    otherwise; else the text's, when the detector is sure; else the default (#340). With a note for the report when
    the text decided it."""
    if opts.language and _LANGUAGE_TAG.match(opts.language.strip()):
        return opts.language.strip(), None
    own = analysis.language.strip() if analysis.language and _LANGUAGE_TAG.match(analysis.language.strip()) else None
    text = " ".join(b.text for p in analysis.pages for b in p.blocks if b.text)
    return document_language(own, text, default)


def _rules(v: dict[str, VeraResult] | None) -> VeraResult | None:
    """The result the repairs go by: PDF/UA-1's, whose rule numbers they know (for a PDF 2.0 file the verdict is
    PDF/UA-2's, #409), else the verdict's."""
    r = (v or {}).get("ua1")
    return r if r and r.available and not r.error else _primary(v or {})


def _primary(v: dict[str, VeraResult]) -> VeraResult | None:
    r = next(iter(v.values()), None)
    return r if r and r.available and not r.error else None


async def _read_formulas(enrichment, images, pdf: Path | None = None) -> None:
    """The formulas the AI read, as MathML and read by rule (#403), and, with the formula reading service, from
    their pictures in `pdf` too; a problem here only means fewer readings."""
    try:
        enrichment.formulas = await asyncio.to_thread(mathml.collect, enrichment.notation, images)
        enrichment.math_disagree = await asyncio.to_thread(mathml.read_by_rule, enrichment.formulas)
    except Exception as exc:  # noqa: BLE001
        log.warning("formulas not read as MathML: %s", exc)
        enrichment.formulas = []
    if pdf is not None and get_settings().formula_ocr_enabled and enrichment.formulas:
        try:
            enrichment.math_picture_disagree = await asyncio.to_thread(mathml.read_from_pictures, enrichment.formulas,
                                                                       pdf)
        except Exception as exc:  # noqa: BLE001
            log.warning("formulas not read from their pictures: %s", exc)
    for f in enrichment.formulas:
        if f.get("disagreement"):
            enrichment.findings.append(f"page {f['page'] + 1}: the AI's reading of a formula and the reading by "
                                       "rule from its LaTeX say different numbers or letters; choose one in Edit tag")
        if f.get("picture_disagreement"):
            enrichment.findings.append(f"page {f['page'] + 1}: the formula read from its picture says different "
                                       "numbers or letters from the AI's transcription; choose a reading in Edit tag")


async def _embed_math(path: Path, enrichment) -> None:
    """Each formula's MathML attached to its tag, in a PDF 2.0 file (#403); before the file is checked, so the
    check covers it. A problem here only means no MathML in the file."""
    if not enrichment.formulas:
        return
    try:
        enrichment.math_embedded = await asyncio.to_thread(mathml_embed.embed, path, enrichment.formulas)
    except Exception as exc:  # noqa: BLE001
        log.warning("MathML not attached to the formulas in %s: %s", path.name, exc)


def _ai_read(enrichment) -> list:
    """The AI's readings for the tag check, each flagged for a person: (page, bbox, kind, spoken[, two readings]).
    A formula whose AI reading and rule reading disagree carries both, so Edit tag can offer the choice."""
    differs = {(f["page"], tuple(f["bbox"] or ())): f for f in enrichment.formulas if mathml.flagged(f)}
    out = []
    for n in enrichment.notation:
        if n.get("by_rule"):
            continue
        f = differs.pop((n["page"], tuple(n.get("bbox") or ())), None)
        out.append((n["page"], n["bbox"], n["kind"], n["spoken"]) + ((mathml.finding_extra(f),) if f else ()))
    # Formulas drawn as pictures (a figure's alt text), where the two readings disagree
    out += [(f["page"], f["bbox"], "formula", f["spoken"], mathml.finding_extra(f)) for f in differs.values()]
    return out


async def _math_outputs(out, stem: str, enrichment) -> tuple[list[dict], dict[str, str]]:
    """The formulas' downloads (#403); a problem here only means no math files."""
    try:
        formulas, files = await asyncio.to_thread(mathml.outputs, out, stem, enrichment.notation,
                                                  formulas=enrichment.formulas)
    except Exception as exc:  # noqa: BLE001
        log.warning("formulas not saved as MathML: %s", exc)
        return [], {}
    enrichment.math_formulas = sum(1 for f in formulas if f["mathml"])
    return formulas, files


# --------------------------------------------------------------------------- main
async def _ocr(base: Path, analysis: DocumentAnalysis, scanned: list[int], ai, opts: JobOptions,
               enrichment: EnrichmentStats, work: Path, progress: Progress) -> tuple[Path, DocumentAnalysis, int, int]:
    """The scanned pages read by OCR (the AI correcting the words Tesseract was unsure of), their figures drawn again
    on their own, and the document analysed again: the file to go on with, its analysis, the pages given a text layer
    and the figures separated."""
    settings = get_settings()
    scan_figure_count = 0
    ocr_path = work / "ocr.pdf"
    # The AI corrects the words Tesseract was unsure of before the text layer is written
    ocr_ai = (ai is not None and opts.ocr_ai and settings.ocr_ai_enabled and settings.ocr_engine != "pymupdf"
              and tesseract_cli() is not None)
    pictures: dict[int, list] = {}  # what OCR read as letters but looks like a picture
    # Scanned pages with text of their own (values typed onto a form), noted before the OCR layer is added
    with fitz.open(base) as before_ocr:
        typed_on = {pno for pno in scanned if before_ocr[pno].get_text("text").strip()}
    ocr_records = await asyncio.to_thread(ocr_lines, str(base), scanned, True, pictures)
    # Pages whose print was placed as pictures of their lines (not a scan): what OCR reads inside a photo or a logo
    # is part of that picture, described with it, not a line of the page
    pictured = {p.number: p for p in analysis.pages if p.number in scanned and p.text_pictures}
    if pictured:
        ocr_records = [rec for rec in ocr_records if rec.page not in pictured or not _in_picture(pictured[rec.page], rec.bbox)]
    if ocr_ai:
        progress("ai_ocr", 22)
        async with ai:
            try:
                await ai.preflight()
            except AIError as exc:
                raise AIProviderUnavailable(f"AI provider '{opts.provider}' is not usable: {exc}") from exc
            await correct_lines(ai, str(base), ocr_records, enrichment)
    ocr_count = await asyncio.to_thread(write_ocr_layer, str(base), str(ocr_path), ocr_records)
    base = ocr_path
    # Figures printed on the scan (charts, photos) are drawn again on their own, clipped, so they can be
    # tagged and described while the rest of the scan is background. The pages look exactly the same.
    text_boxes: dict[int, list] = {}
    for rec in ocr_records:
        text_boxes.setdefault(rec.page, []).append(rec.bbox)
    figures_path = work / "scan_figures.pdf"
    try:
        # Not on a page with typed text: a figure is drawn again on top of the page, which would cover the typed
        # values with the paper of the scan (and a filled-in form's rules aren't a figure)
        scan_regions = await asyncio.to_thread(scan_figures.separate, str(base), str(figures_path),
                                               [p for p in scanned if p not in typed_on and p not in pictured],
                                               text_boxes, pictures)
    except Exception as exc:  # noqa: BLE001 - a problem here only means the figures stay untagged
        log.warning("figures on scanned pages not separated: %s", exc)
        scan_regions = {}
    if scan_regions:
        base = figures_path
    analysis = await asyncio.to_thread(prepare_analysis, base, {rec.page for rec in ocr_records}, ocr_records)
    # The OCR text now carries the content; the page scan itself becomes a background artifact (a picture: a large
    # drawing, a table's grid, is no scan)
    for p in analysis.pages:
        if p.number in scanned:
            for img in p.images:
                if img.xref and covers_page(img.bbox, p.width, p.height):
                    img.decorative = True
            if p.number in pictured:
                _read_pictures_to_background(p, text_boxes.get(p.number, []))
            if scan_regions.get(p.number):
                scan_figure_count += scan_figures.add_to_page(p, scan_regions[p.number])
                p.reading_order = heuristic_reading_order(p)
            p.reading_order = [e for e in p.reading_order if not (
                (el := p.element(e)) is not None and getattr(el, "decorative", False))]
    return base, analysis, ocr_count, scan_figure_count

def _in_picture(page, bbox) -> bool:
    """Whether an OCR line lies in one of a page's pictures that isn't a line of print (a photo, a logo)."""
    area = bbox_area(bbox) or 1.0
    return any(img.kind == "image" and not img.decorative and not line_shaped(img.bbox)
               and bbox_intersection(bbox, img.bbox) >= 0.5 * area for img in page.images)


def _read_pictures_to_background(page, lines: list) -> None:
    """A picture of a line of print that OCR read is that text's background now: an artifact, as a scan is. One it
    didn't read (white letters on a dark band) stays a Figure, described."""
    for img in page.images:
        if img.kind != "image" or img.decorative or not line_shaped(img.bbox):
            continue
        read = sum(bbox_intersection(img.bbox, b) for b in lines)
        if read >= 0.3 * (bbox_area(img.bbox) or 1.0):  # (the picture has a margin round its letters)
            img.decorative = True
            img.alt = None


async def run_remediation(store: JobStore, opts: JobOptions, progress: Progress = lambda s, p: None) -> dict:
    settings = get_settings()
    started = time.monotonic()
    progress = clock = StageClock(progress)
    current_clock.set(clock)
    work, out, stem = store.work_dir, store.output_dir, opts.stem
    work.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    src = store.input_pdf
    warnings: list[str] = protection.notes(protection.inspect(src), opts.report_only)  # signed, restricted (#339)
    # The viewer's tag map of the original needs nothing the pipeline produces and costs about as much as a
    # verification pass (it reads every page back through pdfplumber). Built on its own thread from the start, it
    # runs while the job waits on the validator and on the AI instead of adding its time at the end.
    original_map = asyncio.create_task(tagmap_file(out, stem, "original", src))

    # 1. before -------------------------------------------------------------------------
    progress("validating_original", 5)
    before_audit = await asyncio.to_thread(audit_pdf, src)
    before_vera = await asyncio.to_thread(validate, src, out, "before", stem)
    if not _primary(before_vera):
        warnings.append(f"veraPDF unavailable: {next(iter(before_vera.values())).error}")

    # 2. analysis + OCR -------------------------------------------------------------------
    progress("analyzing", 15)
    # Fill missing Unicode maps first (rendering unchanged), so analysis and the AI read real text.
    base = src
    unicode_maps = await asyncio.to_thread(add_missing_tounicode, src, work / "tounicode.pdf")
    if unicode_maps:
        base = work / "tounicode.pdf"
    # Check marks of unchecked boxes are one ZapfDingbats glyph, a font forms don't embed: drawn as shapes instead
    form_marks = await asyncio.to_thread(redraw_dingbat_marks, base, work / "form_marks.pdf")
    if form_marks:
        base = work / "form_marks.pdf"
    analysis = await asyncio.to_thread(prepare_analysis, base)
    scanned = [p.number for p in analysis.pages if p.is_scanned]
    enrichment = EnrichmentStats()
    ai = get_provider(opts.provider, opts.model, log_path=(work / "ai_log.jsonl") if opts.debug else None)
    if ai is not None:
        ai.tags = {"job": opts.job_id, **({"tier": opts.ai_tier} if opts.ai_tier else {})}
    ocr_count = 0
    scan_figure_count = 0
    if scanned:
        if settings.ocr_enabled and tesseract_available():
            base, analysis, ocr_count, scan_figure_count = await _ocr(base, analysis, scanned, ai, opts, enrichment, work,
                                                                      progress)
        else:
            warnings.append(f"{len(scanned)} scanned page(s) without a text layer; OCR is unavailable")

    # Music: each example becomes one figure, read by the music reading service when there is one
    music_count = 0
    music_files: list[tuple[str, bytes]] = []  # the notation of each example that was read (MusicXML)
    music_score: list[tuple[str, bytes]] = []  # sheet music: the whole piece read as one (MusicXML)
    try:
        music_started = time.monotonic()
        music_count = await asyncio.to_thread(music.apply, base, analysis, enrichment.music, music_files, music_score,
                                              enrichment.findings, enrichment)
        if music_count:
            clock.add("music", time.monotonic() - music_started)
        for m in enrichment.music:
            if m["read"]:
                enrichment.findings.append(f"page {m['page'] + 1}: music read by the music reading service "
                                           f"({m['notes']} notes); check its alt text against the notes")
    except Exception as exc:  # noqa: BLE001 - a problem here only means music is handled as before
        log.warning("music examples not handled: %s", exc)
    enrichment.music_score = len(music_score)
    music_archive = music_score_file = None
    if music_files:
        music_archive = f"{stem}_music.zip"
        await asyncio.to_thread(music.write_archive, out / music_archive, music_score + music_files, f"{stem}.pdf")
    if len(music_score) == 1:
        # The whole piece as one file is what a musician opens: on its own, beside the zip of every line
        music_score_file = f"{stem}_music.{music_score[0][0].rsplit('.', 1)[-1]}"
        (out / music_score_file).write_bytes(music_score[0][1])

    # Form fields named from their printed labels (#341); the AI names the rest below
    fields_named, fields_unlabeled = await asyncio.to_thread(field_labels.find, base)

    # 3. AI ---------------------------------------------------------------------------------
    progress("ai_enrichment", 30)
    if ai is not None:
        async with ai:
            try:
                await ai.preflight()
            except AIError as exc:
                raise AIProviderUnavailable(f"AI provider '{opts.provider}' is not usable: {exc}") from exc
            enrichment = await enrich(ai, base, analysis, dpi=min(settings.raster_dpi, 150), notation=opts.notation_ai,
                                      stats=enrichment)
            await field_labels.ask_ai(ai, base, fields_unlabeled, fields_named, settings.ai_field_labels_max, enrichment)
    else:
        fields_named.unnamed += sorted({f.name for f in fields_unlabeled})
    if fields_named.from_page or fields_named.from_ai or fields_named.unnamed:
        warnings.append(
            f"Form fields: {fields_named.from_page} named from their printed labels"
            + (f", {fields_named.from_ai} by the AI (check them)" if fields_named.from_ai else "")
            + (f"; {len(fields_named.unnamed)} still have only an internal name (set them in the tag manager)"
               if fields_named.unnamed else "") + ".")
    if ai is not None:
        if ai.failures and not ai.calls:
            warnings.append(f"every AI call failed ({ai.failures}); alt text, tables and reading order fell back to "
                            "heuristics. See ai_findings for the error")
        elif ai.failures:
            warnings.append(f"{ai.failures} of {ai.calls + ai.failures} AI calls failed; see ai_findings")
    else:
        if any(not i.decorative for p in analysis.pages for i in p.images):
            warnings.append("made without AI: figures are tagged without alt text, each listed for a person to "
                            "describe")
    # Formulas as MathML, and read by rule (MathCAT) to check the AI's reading of them (#403): before the tags are
    # checked, so a formula whose two readings disagree is flagged there
    await _read_formulas(enrichment, [i for p in analysis.pages for i in p.images], base)
    if opts.debug:
        (work / "analysis.json").write_text(json.dumps(analysis.to_dict(), indent=1, default=str))

    metrics = {
        "pages_processed": analysis.page_count,
        **ai_usage(ai),
        "ai_provider": opts.provider,
        "ai_model": ai.model if ai else None,
        "images_described": enrichment.images_described,
        "music_examples": music_count,
        "music_read": sum(1 for m in enrichment.music if m["read"]),
        "notation_read_by_ai": sum(1 for n in enrichment.notation if not n.get("by_rule")),
        "notation_by_rule": sum(1 for n in enrichment.notation if n.get("by_rule")) + enrichment.dose_cells,
        "notation_ai_rejected": enrichment.notation_rejected,
        "notation_ai_rejected_by_reason": dict(sorted(enrichment.notation_rejected_by.items(), key=lambda kv: -kv[1])),
        "tables_analyzed": enrichment.tables_analyzed,
        "reading_order_pages_ai": enrichment.reading_order_pages,
        "ocr_pages": len(scanned) if ocr_count else 0,
        "scan_figures": scan_figure_count,
        "ocr_ai_words_corrected": sum(len(o["changes"]) for o in enrichment.ocr if o["mode"] == "correct"),
        "ocr_ai_lines_transcribed": sum(1 for o in enrichment.ocr if o["mode"] == "transcribe"),
        "ocr_ai_rejected": enrichment.ocr_rejected,
    }
    before_snapshot = snapshot(before_audit, before_vera)

    if opts.report_only:
        summary = _report_only_summary(analysis, before_snapshot, enrichment)
        verification = None
        if settings.verify_enabled and analysis_has_tags(src):
            progress("verifying_tags", 80)
            rep = await verify_output(src, VerifyOptions(ai=opts.verify_ai, full_review=opts.full_review,
                                                         seed=opts.job_id, kinds=_kinds(analysis),
                                                         judge_model=opts.judge_model))
            verification = {"original": rep.to_dict()}
        tag_maps = {"original": await original_map}
        timed(metrics, started, clock)
        metrics["limits_hit"] = limits.collect(enrichment.limits_hit, verification, enrichment.findings,
                                               metrics.get("pages_processed"))
        result = RunResult(
            files={"remediated_primary": None, "remediated_visual_fallback": None},
            metrics=metrics,
            reports={
                "verapdf_before": xml_name(before_vera),
                "verapdf_after": None,
                "comparison": {"before": before_snapshot, "after": None,
                               "ai_audit": _ai_audit(analysis, enrichment)},
                "remediation_summary": summary,
                "ai_findings": enrichment.findings[:100],
                "tag_verification": verification,
                "tag_maps": tag_maps,
            },
            warnings=warnings,
        ).to_dict()
        write_json_report(out, stem, result, store)
        return result

    # 4. visual (non-destructive) ----------------------------------------------------------
    progress("tagging", 55)
    title = _pick_title(opts, analysis)
    language, language_note = _pick_language(opts, analysis, settings.default_language)
    if language_note:
        warnings.append(language_note)
    visual = work / "visual.pdf"
    visual_region_alts: list = []

    async def tag(pdf: Path, dst: Path, analysis: DocumentAnalysis, layout_tables, region_alts=None) -> TagResult:
        """The document tagged, as every stage tags it: the same title, language, field names and options."""
        return await asyncio.to_thread(
            tag_document, pdf, dst, analysis, title=title, language=language, field_labels=fields_named,
            ignore_existing=opts.ignore_existing, pdfua_part=settings.pdfua_part, layout_tables=layout_tables,
            region_alts=region_alts)

    tag_visual = await tag(base, visual, analysis, enrichment.layout_tables)
    if tag_visual.passages_lang:
        warnings.append(f"Language: {tag_visual.passages_lang} passage(s) in another language than the document's were "
                        "tagged with their own.")
    if tag_visual.figures_needing_alt and ai is not None:
        # Existing Figure tags over vector art or equations: describe the rendered region, re-tag.
        progress("ai_existing_figures", 62)
        async with ai:
            region_alts = await _describe_regions(ai, base, tag_visual.figures_needing_alt, enrichment)
        metrics.update(ai_usage(ai))
        if region_alts and any(decorative for _, _, _, decorative in region_alts):
            # A figure judged decorative loses its tag and its content becomes an artifact: that takes the content
            # marker, so the document is tagged again with every verdict
            tag_visual = await tag(base, visual, analysis, enrichment.layout_tables, region_alts)
        elif region_alts:
            # Descriptions alone go straight onto the tags they describe; nothing else about the file changes
            written = await asyncio.to_thread(write_figure_alts, visual, tag_visual.figure_handles,
                                              [(p, b, alt) for p, b, alt, _ in region_alts])
            apply_written_alts(tag_visual, written)
        if region_alts:
            visual_region_alts = region_alts
    # The document's own formulas (MathML its kept tags carry, #403): with the others, in place of any AI reading of
    # them
    if tag_visual.document_formulas:
        mathml.add_document_formulas(enrichment.formulas, enrichment.notation, tag_visual.document_formulas)
    await _embed_math(visual, enrichment)
    progress("validating_output", 70)
    vera_visual = await asyncio.to_thread(validate, visual, work, "after_visual", stem)
    # Width corrections are exact on paper, but many of them can shift glyphs by a fraction of a
    # point in real renderers. Keep them in the visual file only if the pages stay pixel-identical;
    # otherwise move them to the modified file.
    widths_subpixel: tuple | None = None  # (path, vera, count) of a width-fixed copy for the modified file
    # Incomplete glyph lists in font descriptors (7.21.4.2): removing them never changes rendering.
    vera_visual, glyph_lists_fixed = await asyncio.to_thread(_fix_glyph_lists, visual, vera_visual, work,
                                                             "after_visual", stem)
    pre_fix = work / "visual_before_widthfix.pdf"
    shutil.copyfile(visual, pre_fix)
    vera_prefix = vera_visual
    vera_visual, widths_fixed = await asyncio.to_thread(_fix_widths, visual, vera_visual, work, "after_visual", stem)
    fidelity: dict | None = None  # of the visual file as it will be returned; the comparison renders every page
    if widths_fixed:
        fidelity = await asyncio.to_thread(visual_fidelity, src, visual)
    if fidelity is not None and not fidelity["identical"]:
        fidelity = None
        wf = work / "visual_widthfixed.pdf"
        shutil.move(visual, wf)
        shutil.copyfile(pre_fix, visual)
        widths_subpixel = (wf, vera_visual, widths_fixed)
        vera_visual, widths_fixed = vera_prefix, 0
        warnings.append("glyph width corrections would move text by a fraction of a point, so they were applied "
                        "to the modified file only; the visually exact file keeps the original widths")

    # 5. modified (destructive) if required ---------------------------------------------
    # Least destructive first: embed metric-compatible substitutes for unembedded standard fonts
    # (tags untouched); then flatten only the pages that still fail.
    modified: Path | None = None
    tag_modified: TagResult | None = None
    vera_modified: dict[str, VeraResult] | None = None
    destructive_pages: list[int] = []
    fonts_embedded: list[str] = []
    pv = _primary(vera_visual)  # the verdict
    rv = _rules(vera_visual)  # what the repairs go by
    needs_fix = widths_subpixel is not None or not (pv and pv.compliant) and (
        (rv is not None and bool(rv.failed_clauses_with(DESTRUCTIVE_CLAUSES)))
        or (rv is None and any(p.font_issues for p in analysis.pages)))
    if needs_fix:
        progress("destructive_fixes", 78)
        stage_src = base
        emb = work / "fonts_embedded.pdf"
        fonts_embedded = await asyncio.to_thread(embed_standard_fonts, base, emb)
        candidate = work / "modified.pdf"
        fonts_only: tuple | None = None  # fallback if flattening is later rejected
        if widths_subpixel is not None and not fonts_embedded:
            # Least destructive candidate: the visual file with the width corrections.
            candidate, vera_modified = widths_subpixel[0], widths_subpixel[1]
            tag_modified = tag_visual
            fonts_only = (candidate, tag_modified, vera_modified)
        if fonts_embedded:
            stage_src = emb
            fonts_candidate = work / "modified_fonts.pdf"
            tag_modified = await tag(stage_src, fonts_candidate, analysis, enrichment.layout_tables, visual_region_alts)
            await _embed_math(fonts_candidate, enrichment)
            vera_modified = await asyncio.to_thread(validate, fonts_candidate, work, "after_modified_fonts", stem)
            vera_modified, _ = await asyncio.to_thread(_fix_glyph_lists, fonts_candidate, vera_modified, work,
                                                       "after_modified_fonts", stem)
            vera_modified, _ = await asyncio.to_thread(_fix_widths, fonts_candidate, vera_modified, work,
                                                       "after_modified_fonts", stem)
            candidate = fonts_candidate
            fonts_only = (fonts_candidate, tag_modified, vera_modified)
        if widths_subpixel is not None and not fonts_embedded:
            stage_src = base  # flattening re-tags from the untagged base; widths are re-fixed afterwards
        pm = _primary(vera_modified) if vera_modified else pv
        unreadable: frozenset[str] = frozenset()
        if not (pm and pm.compliant):
            rules_now = _rules(vera_modified) if vera_modified else rv
            destructive_pages = await asyncio.to_thread(_destructive_candidates, rules_now, analysis, stage_src)
            # Fonts whose map gives glyphs U+0000 (7.21.7, test 2): their text is OCR'd for the text layer, not copied.
            # A font with no map at all (test 1) is still read through its encoding, as before
            unreadable = frozenset(n for f in (rules_now.failures if rules_now else []) if f.clause == "7.21.7"
                                   and f.test == "2" for n in f.fonts)
        if destructive_pages:
            progress("flattening", 84)
            flat = work / "flattened.pdf"
            candidate = work / "modified.pdf"
            vectors = {p.number: [i.bbox for i in p.images if i.kind == "vector" or (i.xref == 0 and not i.decorative)]
                       for p in analysis.pages if p.number in destructive_pages}
            await asyncio.to_thread(flatten_pages, str(stage_src), str(flat), destructive_pages, vectors,
                                    unreadable)
            analysis_mod = await asyncio.to_thread(prepare_analysis, flat, scanned if ocr_count else ())
            music.carry_over(analysis, analysis_mod)
            layout_mod = transfer_enrichment(analysis, analysis_mod, set(destructive_pages), enrichment.layout_tables,
                                             notation=enrichment.notation)
            tag_modified = await tag(flat, candidate, analysis_mod, layout_mod, visual_region_alts)
            await _embed_math(candidate, enrichment)
            vera_modified = await asyncio.to_thread(validate, candidate, work, "after_modified", stem)
            vera_modified, _ = await asyncio.to_thread(_fix_glyph_lists, candidate, vera_modified, work,
                                                       "after_modified", stem)
            vera_modified, _ = await asyncio.to_thread(_fix_widths, candidate, vera_modified, work, "after_modified", stem)
        if vera_modified is not None:
            verdict = await asyncio.to_thread(_judge_candidate, pv, vera_modified, visual, candidate)
            if verdict and destructive_pages and fonts_only is not None:
                # Flattening cost too much structure: fall back to the fonts-only version.
                warnings.append(f"page flattening was not applied: {verdict}")
                candidate, tag_modified, vera_modified = fonts_only
                destructive_pages = []
                verdict = await asyncio.to_thread(_judge_candidate, pv, vera_modified, visual, candidate)
            if verdict:
                warnings.append(f"the modified version was discarded: {verdict}; only the visually exact output is returned")
                modified, tag_modified, vera_modified, destructive_pages, fonts_embedded = None, None, None, [], []
            else:
                modified = candidate

    # 6. outputs + after reports ---------------------------------------------------------
    progress("reporting", 90)
    files: dict[str, str | None] = {"remediated_primary": None, "remediated_visual_fallback": None}
    vera_after: dict[str, str | None] = {"primary": None, "visual_fallback": None}
    if modified is not None:
        primary_name, fallback_name = f"{stem}_compliant_modified.pdf", f"{stem}_compliant_visual.pdf"
        shutil.copyfile(modified, out / primary_name)
        shutil.copyfile(visual, out / fallback_name)
        files = {"remediated_primary": primary_name, "remediated_visual_fallback": fallback_name}
        vera_after["primary"] = move_xml(vera_modified, out, f"{stem}_verapdf_after")
        vera_after["visual_fallback"] = move_xml(vera_visual, out, f"{stem}_verapdf_after_visual")
        primary_vera, primary_path = vera_modified, out / primary_name
    else:
        primary_name = f"{stem}_remediated.pdf"
        shutil.copyfile(visual, out / primary_name)
        files["remediated_primary"] = primary_name
        vera_after["primary"] = move_xml(vera_visual, out, f"{stem}_verapdf_after")
        primary_vera, primary_path = vera_visual, out / primary_name

    # PDF/UA-2 (#445): the finished files made PDF 2.0 and PDF/UA-2, in place ("2"), or a PDF/UA-2 copy of the main
    # file beside it ("both"), each checked again. A document that came as PDF 2.0 is PDF/UA-2 already, but its own
    # tags, when kept, still need what PDF/UA-2 asks of a PDF/UA-1 file (#413): the same step, in place, whatever was
    # chosen; what it changed is noted apart, as nothing was converted
    pdfua2: dict = {}
    pdf2_repairs: dict = {}
    toc_guesses: dict[str, list] = {}  # by output: contents entries pointed at a heading by their text, to check
    native2 = _pdf_version(out / files["remediated_primary"]) >= "2.0"
    if native2:
        progress("pdfua2", 91)
        if opts.pdfua in ("2", "both"):
            warnings.append("the document is PDF 2.0, which can only be PDF/UA-2: it is delivered as PDF/UA-2 only")
        for key, label in (("remediated_primary", "primary"), ("remediated_visual_fallback", "visual_fallback")):
            if files.get(key):
                changes, vera2 = await asyncio.to_thread(_as_pdfua2, out / files[key], out / files[key], work, stem,
                                                         label, toc_guesses.setdefault(label, []))
                pdf2_repairs[label] = changes[1:]  # not the first line: it was PDF 2.0 and PDF/UA-2 already
                vera_after[label] = move_xml(vera2, out, f"{stem}_verapdf_after"
                                             + ("" if label == "primary" else "_visual"))
                if label == "primary":
                    primary_vera = vera2
                else:
                    vera_visual = vera2
        if modified is None:
            vera_visual = primary_vera
    elif opts.pdfua in ("2", "both"):
        progress("pdfua2", 91)
        if opts.pdfua == "2":
            for key, label in (("remediated_primary", "primary"), ("remediated_visual_fallback", "visual_fallback")):
                if files.get(key):
                    pdfua2[label], vera2 = await asyncio.to_thread(
                        _as_pdfua2, out / files[key], out / files[key], work, stem, label,
                        toc_guesses.setdefault(label, []))
                    vera_after[label] = move_xml(vera2, out, f"{stem}_verapdf_after"
                                                  + ("" if label == "primary" else "_visual"))
                    if label == "primary":
                        primary_vera = vera2
                    else:
                        vera_visual = vera2
            if modified is None:
                vera_visual = primary_vera
        else:
            name = f"{stem}_compliant_pdfua2.pdf"
            pdfua2["primary"], vera2 = await asyncio.to_thread(_as_pdfua2, out / files["remediated_primary"],
                                                                out / name, work, stem, "primary_pdfua2")
            files["remediated_pdfua2"] = name
            vera_after["pdfua2"] = move_xml(vera2, out, f"{stem}_verapdf_after_pdfua2")
            pdfua2["snapshot"] = snapshot(await asyncio.to_thread(audit_pdf, out / name), vera2)

    verification = None
    # Tag maps for the portal's viewer: the original and every output
    tag_maps = {"original": await original_map}
    # Bookmarks from the headings go in once each output's tag map says where the headings are (after veraPDF, which
    # doesn't check the outline; the tag tree, and so the tag map, don't change)
    bookmarks: dict[str, dict] = {}
    if settings.verify_enabled:
        progress("verifying_tags", 92)
        verification = {}
        vopts = VerifyOptions(ai=opts.verify_ai, full_review=opts.full_review, seed=opts.job_id,
                              kinds=_kinds(analysis), judge_model=opts.judge_model,
                              ai_read=_ai_read(enrichment)
                              + [(o["page"], o["bbox"], f"ocr-{o['mode']}", o["after"]) for o in enrichment.ocr],
                              review_cache={})  # one verdict per figure across both output files
        outputs = [("primary", out / files["remediated_primary"])]
        if files.get("remediated_visual_fallback"):
            outputs.append(("visual_fallback", out / files["remediated_visual_fallback"]))
        for label, path in outputs:
            rep = await verify_output(path, replace(vopts, toc_guesses=toc_guesses.get(label, [])))
            if not rep.integrity_passed:
                raise IntegrityError(label, rep.errors)
            verification[label] = rep.to_dict()
            bookmarks[label], data = await add_bookmarks(path, rep.tagmap)
            tag_maps[label] = await tagmap_file(out, stem, label, path, data)
            if rep.ai.get("error") and rep.ai["error"] not in " ".join(warnings):
                warnings.append(f"AI review did not run: {rep.ai['error']}")
            if rep.ai.get("ai_tokens_used"):
                tok = metrics.setdefault("verification_ai_tokens", {"prompt_tokens": 0, "completion_tokens": 0})
                for k in tok:
                    tok[k] += rep.ai["ai_tokens_used"].get(k, 0)
                metrics["verification_ai_cost_usd"] = round(metrics.get("verification_ai_cost_usd", 0.0)
                                                            + float(rep.ai["ai_tokens_used"].get("cost_usd", 0.0)), 6)

    for label, key in (("primary", "remediated_primary"), ("visual_fallback", "remediated_visual_fallback")):
        if files.get(key) and label not in tag_maps:
            bookmarks[label], data = await add_bookmarks(out / files[key], None)
            tag_maps[label] = await tagmap_file(out, stem, label, out / files[key], data)

    after_audit = await asyncio.to_thread(audit_pdf, primary_path)
    after_snapshot = snapshot(after_audit, primary_vera)
    comparison = {
        "before": before_snapshot,
        "after": after_snapshot,
        "delta": delta(before_snapshot, after_snapshot),
        "existing_tags": tag_visual.existing_tags,
        "visual_fidelity": fidelity or await asyncio.to_thread(visual_fidelity, src, visual),
        "glyph_widths_compensated": widths_fixed,
        "glyph_widths_modified_only": widths_subpixel[2] if widths_subpixel else 0,
        "glyph_lists_removed": glyph_lists_fixed,
        "unicode_maps_added": unicode_maps,
        "form_marks_redrawn": form_marks,
        "bookmarks": bookmarks.get("primary"),
    }
    if pdfua2:
        # What making it PDF/UA-2 changed; with "both", the PDF/UA-2 copy's own verdict
        comparison["pdfua2_changes"] = pdfua2.get("primary")
        if "snapshot" in pdfua2:
            comparison["after_pdfua2"] = pdfua2["snapshot"]
    if any(pdf2_repairs.values()):
        # A PDF 2.0 document's own tags given what PDF/UA-2 asks (#413)
        comparison["pdf2_repairs"] = pdf2_repairs.get("primary")
    if modified is not None:
        in_place = (opts.pdfua == "2" and pdfua2) or (native2 and "visual_fallback" in pdf2_repairs)
        visual_now = out / files["remediated_visual_fallback"] if in_place else visual
        comparison["after_visual_fallback"] = snapshot(await asyncio.to_thread(audit_pdf, visual_now), vera_visual)
        comparison["destructive_pages"] = [p + 1 for p in destructive_pages]
        comparison["fonts_embedded"] = fonts_embedded

    # Formulas as MathML (#403): the structured form of each one the AI read, as music has MusicXML. Before the
    # summary, which counts them
    math_formulas, math_files = await _math_outputs(out, stem, enrichment)

    metrics["limits_hit"] = limits.collect(enrichment.limits_hit, verification, enrichment.findings,
                                           metrics.get("pages_processed"))
    summary = _summary(analysis, tag_visual, tag_modified, enrichment, before_snapshot, after_snapshot,
                       comparison, destructive_pages, warnings, title, language)
    result = RunResult(
        files=files,
        metrics=metrics,
        reports={
            "verapdf_before": xml_name(before_vera),
            "verapdf_after": vera_after if any(vera_after.values()) else None,
            "comparison": comparison,
            "remediation_summary": summary,
            "tagging": {"visual": _tag_report(tag_visual), "modified": _tag_report(tag_modified) if tag_modified else None},
            "tag_verification": verification,
            "tag_maps": tag_maps,
            "ai_findings": enrichment.findings[:100],
            # Specialist notation the AI read (page 1-based, region, kind, read-aloud text, LaTeX, confidence)
            "ai_notation": [{**n, "page": n["page"] + 1} for n in enrichment.notation][:300] or None,
            # OCR lines the AI corrected or transcribed (page 1-based, region, mode, before, after, changed words)
            "ai_ocr": [{**o, "page": o["page"] + 1} for o in enrichment.ocr][:500] or None,
            # Music examples (page 1-based, region, whether the music reading service read it, its reading or why not)
            "music": [{**m, "page": m["page"] + 1} for m in enrichment.music][:300] or None,
            # The notation of the examples that were read, as MusicXML files in one zip
            "music_files": music_archive,
            # Sheet music: the whole piece as one MusicXML file (several movements: they're in the zip)
            "music_score": music_score_file,
            # Formulas the AI read (page 1-based, region, read-aloud text, LaTeX, MathML) and their downloads (#403)
            "math": [{**f, "page": f["page"] + 1} for f in math_formulas][:300] or None,
            # Formulas whose AI reading and rule reading disagree: both readings, and the person's choice once made
            "math_disagreements": mathml.disagreements(math_formulas) or None,
            **{k: math_files.get(k) for k in ("math_files", "math_list", "math_nemeth", "math_ueb")},
        },
        warnings=warnings,
    ).to_dict()
    # The whole run up to here; writing the reports comes after and can't be in them
    timed(metrics, started, clock)
    log.info("job %s: %d pages in %.0f s; by stage: %s", opts.job_id, metrics.get("pages_processed") or 0,
             metrics["processing_time_seconds"], json.dumps(metrics["stage_seconds"]))
    write_json_report(out, stem, result, store)
    progress("completed", 100)
    return result


def _tag_report(result: TagResult) -> dict:
    """A TagResult for the report: everything but the handles that only serve the job itself."""
    out = asdict(result)
    out.pop("figure_handles", None)
    return out


def _kinds(analysis: DocumentAnalysis) -> list:
    """Content types from the alt-text pass, used to route figures to the right judge."""
    return [(p.number, i.bbox, i.kind) for p in analysis.pages for i in p.images if i.kind]


def analysis_has_tags(pdf: Path) -> bool:
    import pikepdf

    with pikepdf.open(pdf) as doc:
        return "/StructTreeRoot" in doc.Root


async def _describe_regions(ai, pdf_path: Path, regions: list, enrichment: EnrichmentStats) -> list:
    from app.ai.base import AIError
    from app.pipeline.analyzer import render_png

    doc = fitz.open(pdf_path)
    slots = render_slots()
    try:
        async def one(page: int, bbox, *_) -> tuple | None:
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
            if (w < 6 and h < 6) or min(w, h) < 2:
                # A few-point dot, or a rule or hairline (Word tags horizontal lines as figures), carries no
                # information: make it an artifact.
                enrichment.findings.append(f"page {page + 1}: {'line' if min(w, h) < 2 and max(w, h) >= 6 else 'tiny'} "
                                           f"figure ({w:.0f}×{h:.1f} pt) marked as an artifact")
                return (page, tuple(bbox), None, True)
            try:
                async with slots:
                    png = render_png(doc, page, dpi=150, clip=bbox, max_px=1200)
                    res = await ai.describe_image(png, page, "")
            except Exception as exc:  # noqa: BLE001 - one figure never fails the job: it's left for review
                enrichment.findings.append(f"page {page + 1}: alt text for existing figure failed ({exc})")
                return None
            if res["decorative"] and not res["alt"]:
                enrichment.findings.append(f"page {page + 1}: existing figure judged decorative; "
                                           "its tag was removed and the content marked as an artifact (review)")
                return (page, tuple(bbox), None, True)
            if not res["alt"]:
                return None
            if res["decorative"]:
                # It is already a Figure tag, which must have alt text: keep the model's description.
                enrichment.findings.append(f"page {page + 1}: existing figure judged decorative; "
                                           "kept as a figure with a short description (review)")
            enrichment.images_described += 1
            return (page, tuple(bbox), res["alt"], False)

        limit = get_settings().ai_existing_figures_max
        results = await asyncio.gather(*(one(*region) for region in regions[:limit]))
    finally:
        doc.close()
    if len(regions) > limit:
        enrichment.limit_hit("AI_EXISTING_FIGURES_MAX", limit, len(regions),
                             "figures the document arrived with weren't described and have no alt text")
        enrichment.findings.append(f"{len(regions) - limit} figure tag(s) the document arrived with, beyond the limit of "
                                   f"{limit} (AI_EXISTING_FIGURES_MAX), weren't described and have no alt text "
                                   f"(from page {regions[limit][0] + 1} on)")
    return [r for r in results if r]


# --------------------------------------------------------------------------- decisions / reporting
def _destructive_candidates(pv: VeraResult | None, analysis: DocumentAnalysis, pdf_path: Path) -> list[int]:
    """Pages to flatten: pages veraPDF attributes destructive-only failures to, plus every page that
    draws text with a failing font (veraPDF reports each font problem only on its first page)."""
    pages: set[int] = set()
    if pv is None:  # no validator: fall back to the analyser's font findings
        return sorted({p.number for p in analysis.pages if p.font_issues})
    pages |= pv.pages_failing(DESTRUCTIVE_CLAUSES)
    fonts = pv.failing_fonts(("7.21",))
    if fonts:
        doc = fitz.open(pdf_path)
        try:
            for i in range(doc.page_count):
                used = {f[3].split("+", 1)[-1] for f in doc[i].get_fonts(full=True)}
                if used & fonts and doc[i].get_text("text").strip():
                    pages.add(i)
        finally:
            doc.close()
    if not pages and pv.unattributed(("7.21",)):
        pages = {p.number for p in analysis.pages if p.font_issues}
    return sorted(p for p in pages if 0 <= p < analysis.page_count)


def _fix_glyph_lists(pdf: Path, vera: dict[str, VeraResult], work: Path, label: str, stem: str
                     ) -> tuple[dict[str, VeraResult], list[str]]:
    """Remove incomplete CIDSet/CharSet glyph lists that veraPDF reports (7.21.4.2), in place, and
    re-validate. Rendering is unchanged: nothing draws with these lists."""
    from app.tagging.font_descriptors import CLAUSE, remove_incomplete_glyph_lists

    pv = _rules(vera)
    if pv is None or CLAUSE not in pv.failed_clauses:
        return vera, []
    tmp = work / f"glyphlists_{pdf.name}"
    fixed = remove_incomplete_glyph_lists(pdf, tmp, [f for f in pv.failures if f.clause == CLAUSE])
    if not fixed:
        return vera, []
    tmp.replace(pdf)
    return validate(pdf, work, label, stem), fixed


def _fix_widths(pdf: Path, vera: dict[str, VeraResult], work: Path, label: str, stem: str
                ) -> tuple[dict[str, VeraResult], int]:
    """Repair 7.21.5 width mismatches in place (pixel-identical), re-validating up to 3 rounds
    because veraPDF lists a limited number of failed checks per rule."""
    total = 0
    for _ in range(3):
        pv = _rules(vera)
        if pv is None or not pv.width_mismatches:
            break
        tmp = work / f"widthfix_{pdf.name}"
        n = fix_width_mismatches(pdf, tmp, pv.width_mismatches)
        if not n:
            break
        tmp.replace(pdf)
        total += n
        vera = validate(pdf, work, label, stem)
    return vera, total


def _judge_candidate(pv: VeraResult | None, vera_candidate: dict, visual: Path, candidate: Path) -> str | None:
    """None if the modified candidate is worth returning, else the reason it is not."""
    pm = _primary(vera_candidate)
    better = (pm is not None and pv is not None and (pm.compliant or pm.failed_checks < pv.failed_checks)) or (
        pv is None and pm is None)
    if not better:
        return "it did not reduce validation failures"
    return _structure_loss(visual, candidate)


def _heading_chars(path: Path) -> int:
    """Amount of text tagged as headings (H, H1-H6), in non-space characters."""
    elements = build_tagmap(path)["elements"]
    return sum(len(re.sub(r"\s", "", e.get("text") or "")) for e in elements
               if re.fullmatch(r"H[1-6]?", e["role"]) and e.get("own"))


def _structure_loss(visual: Path, candidate: Path) -> str | None:
    """Reject a modified file that loses meaningful structure compared with the visual one."""
    a, b = audit_pdf(visual), audit_pdf(candidate)
    for key, label in (("figures", "figures"), ("tables", "tables"), ("headings", "headings"),
                       ("struct_elements", "structure elements")):
        if a.get(key, 0) >= 3 and b.get(key, 0) < 0.8 * a[key]:
            # Fewer heading tags is fine when the same heading text is still tagged as headings: a
            # flattened page can read a two-line title as one heading.
            if key == "headings" and _heading_chars(candidate) >= 0.9 * _heading_chars(visual) > 0:
                continue
            return f"it would drop {label} from {a[key]} to {b.get(key, 0)}"
    return None


def _pdf_version(path: Path) -> str:
    with pikepdf.open(path) as pdf:
        return str(pdf.pdf_version)


def _as_pdfua2(src: Path, dst: Path, work: Path, stem: str, label: str,
               guessed: list | None = None) -> tuple[list[str], dict[str, VeraResult]]:
    """A finished PDF/UA-1 file as PDF/UA-2 (app.tagging.pdfua2), written to dst (src itself for in place) and
    checked: what changed, and veraPDF's results (PDF/UA-2's first). guessed gets the contents entries pointed at a
    heading by their text (pdfua2.upgrade)."""
    from app.tagging.pdfua2 import upgrade
    tmp = work / f"pdfua2_{label}.pdf"
    changes = upgrade(src, tmp, guessed)
    shutil.copyfile(tmp, dst)
    return changes, validate(dst, work, f"after_{label}_pdfua2", stem)


def _ai_audit(analysis: DocumentAnalysis, enrichment: EnrichmentStats) -> dict:
    return {
        "figures": [{"page": i.page + 1, "bbox": i.bbox, "decorative": i.decorative, "suggested_alt": i.alt,
                     "kind": i.kind} for p in analysis.pages for i in p.images][:200],
        "tables": [{"page": t.page + 1, "rows": t.n_rows, "cols": t.n_cols, "header_rows": t.header_rows,
                    "header_cols": t.header_cols, "layout_table": t.id in enrichment.layout_tables,
                    "summary": t.summary} for p in analysis.pages for t in p.tables][:100],
        "multi_column_pages": [p.number + 1 for p in analysis.pages if p.columns > 1],
        "notation": [{**n, "page": n["page"] + 1} for n in enrichment.notation][:200],
        "music": [{**m, "page": m["page"] + 1} for m in enrichment.music][:200],
        "findings": enrichment.findings[:100],
    }


def _report_only_summary(analysis, before: dict, enrichment: EnrichmentStats) -> str:
    lines = ["Report only: no changes were applied.", verdict_line("Original", before)]
    lines.append(f"Tagged: {'yes' if before.get('tagged') else 'no'}; "
                 f"{before.get('content_tagged_ratio', 0):.0%} of content is tagged or marked as artifact.")
    figs = sum(1 for p in analysis.pages for i in p.images if not i.decorative)
    lines.append(f"Detected {figs} informative figure(s), {sum(len(p.tables) for p in analysis.pages)} table(s), "
                 f"{sum(1 for p in analysis.pages if p.columns > 1)} multi-column page(s).")
    if enrichment.images_described:
        lines.append(f"AI drafted alt text for {enrichment.images_described} figure(s) (see comparison.ai_audit).")
    return "\n".join(lines)


def music_summary(records: list[dict]) -> str:
    """How much music there is and how it was tagged: lines or examples, and the figures they became (a line that
    was read is one figure per measure where the page's bar lines agree with the reading; a scanned score is one
    picture, however many lines it has)."""
    n = len(records)
    in_pictures = sum(1 for m in records if m.get("figures") == 0)
    figures = sum(m.get("figures", 1) for m in records)
    split = sum(1 for m in records if m.get("figures", 1) > 1)
    line = f"Music notation: {n} line{'s' if n != 1 else ''} or example{'s' if n != 1 else ''} of music"
    if split:
        line += (f", tagged as {figures} figure{'s' if figures != 1 else ''} ({split} of them one figure per measure; "
                 "the others one figure each)")
    elif figures:
        line += ", each tagged as one figure"
    if in_pictures:
        line += (f"{'; ' if figures else ', '}{in_pictures} {'are' if in_pictures != 1 else 'is'} part of a picture "
                 "(a scanned score), which stays one figure")
    return line


def _summary(analysis, tv: TagResult, tm: TagResult | None, enr: EnrichmentStats, before: dict, after: dict,
             comparison: dict, destructive_pages: list[int], warnings: list[str], title: str, language: str) -> str:
    lines = [verdict_line("Before", before), verdict_line("After", after)]
    bm = comparison.get("bookmarks") or {}
    if bm.get("bookmarks"):
        lines.append(f"Bookmarks: {bm['bookmarks']} made from the headings"
                     + (", replacing bookmarks that went nowhere." if bm.get("outline") == "broken" else "."))
    elif bm.get("outline") == "kept":
        lines.append("Bookmarks: the document's own were kept.")
    et = tv.existing_tags
    if et.get("present"):
        if et.get("rebuilt_tree"):
            lines.append(f"Existing tags discarded and rebuilt ({et.get('reason')}).")
        else:
            lines.append(f"Existing tags: {et['retained']} retained, {et['repaired']} repaired, "
                         f"{et['replaced']} replaced, {et.get('removed', 0)} empty removed, "
                         f"{et.get('artifacted', 0)} decorative figure(s) made artifacts; "
                         f"{tv.orphans_retagged} orphaned content item(s) re-tagged."
                         + (f" {tv.empty_spans_removed} empty span(s) folded into the tag they were in."
                            if tv.empty_spans_removed else "")
                         + (f" {tv.form_pages_retagged} figure tag(s) holding a whole page of text drawn as one "
                            "picture: that text tagged afresh from the page's layout."
                            if tv.form_pages_retagged else "")
                         + (f" {tv.groupings_artifacted} grouping tag(s) holding only lines or shapes made "
                            "decoration." if tv.groupings_artifacted else ""))
            if tv.added_placed:
                lines.append(f"Added to the existing tags: {tv.added_placed} tag(s) (content the old tags left out, "
                             "untagged form fields and links) placed beside the tags they follow on their page"
                             + (f"; {tv.added_headings_demoted} of them looked like headings and were made paragraphs, "
                                "as the document has its own headings." if tv.added_headings_demoted else "."))
        for f in et.get("findings", [])[:5]:
            lines.append(f"  - {f}")
    el = tv.elements
    headings = sum(v for k, v in el.items() if k.startswith("H") and k[1:].isdigit())
    lines.append(
        f"Tags added: {tv.tagged_units} content sequences tagged, {tv.artifacts} marked as artifacts; "
        f"{headings} heading(s), {el.get('P', 0)} paragraph(s), {el.get('L', 0)} list(s), "
        f"{tv.tables} table(s), {tv.figures_with_alt + tv.figures_without_alt} figure(s), {tv.links} link(s)."
    )
    if tv.footnotes or tv.notes_given_ids:
        lines.append("Footnotes: " + "; ".join(
            ([f"{tv.footnotes} tagged as notes, each linked from the text with its mark"] if tv.footnotes else [])
            + ([f"{tv.notes_given_ids} of the document's own notes given the ID PDF/UA requires"]
               if tv.notes_given_ids else [])) + ".")
    if tv.tables_linked:
        lines.append(f"Complex tables: {tv.tables_linked} with several levels of headers, each cell linked to its "
                     "headers (Headers and ID).")
    lines.append(f"Alt text: {enr.images_described} generated by AI, {enr.images_decorative} image(s) judged decorative"
                 + (f", {tv.figures_without_alt} figure(s) still without alt text" if tv.figures_without_alt else "") + ".")
    if enr.tables_analyzed:
        lines.append(f"Tables: {enr.tables_analyzed} analyzed by AI for header rows/columns and merged cells"
                     + (f"; {len(enr.layout_tables)} identified as layout tables" if enr.layout_tables else "") + ".")
    if enr.ocr:
        words = sum(len(o["changes"]) for o in enr.ocr if o["mode"] == "correct")
        lines_t = sum(1 for o in enr.ocr if o["mode"] == "transcribe")
        lines.append(f"OCR: the AI corrected {words} uncertain word(s) and transcribed {lines_t} line(s) Tesseract "
                     "couldn't read (each is flagged for review)"
                     + (f"; {enr.ocr_rejected} AI correction(s) failed the checks and weren't used" if enr.ocr_rejected else "")
                     + ".")
    if tv.picture_glyphs_mapped:
        n = sum(tv.picture_glyphs_mapped.values())
        lines.append(f"Characters that are pictures: {n} character(s) in {len(tv.picture_glyphs_mapped)} font(s) with no "
                     "meaning as text (music note heads, pieces of drawn lines), in figures and decoration, were "
                     "given private-use Unicode values so every font is fully mapped; rendering unchanged."
                     + (f" On page(s) {', '.join(map(str, tv.picture_glyphs_in_text[:20]))} such a symbol stands in a sentence "
                        "and has no spoken form: give it replacement text in the tag editor."
                        if tv.picture_glyphs_in_text else ""))
    if enr.music:
        read = sum(1 for m in enr.music if m["read"])
        line = music_summary(enr.music)
        if read:
            line += (f"; {read} read note by note by the music reading service (check them: a reading can have wrong "
                     "notes)" + (f", {len(enr.music) - read} described as pictures" if len(enr.music) - read else "")
                     + ". Their notation is in the music files (MusicXML, one per example): someone who reads music "
                       "can open them in notation software to play them back against the page"
                     + (". The whole piece was also read as one score" if enr.music_score else ""))
        lines.append(line + ".")
    by_ai = sum(1 for n in enr.notation if not n.get("by_rule"))
    by_rule = len(enr.notation) - by_ai
    if by_ai or enr.notation_rejected:
        lines.append(f"Specialist notation: {by_ai} block(s) the layout rules couldn't read were read by AI "
                     "(each is flagged for review)"
                     + (f"; {enr.notation_rejected} AI reading(s) didn't match the page and weren't used" if enr.notation_rejected else "")
                     + ".")
    if by_rule or enr.dose_cells:
        lines.append(f"Dosage notation: {by_rule} block(s) and {enr.dose_cells} table cell(s) put in words by rule (units "
                     "by name, prescription shorthand in full); no AI was used for these.")
    if enr.math_embedded:
        lines.append(f"Formulas: MathML attached to {enr.math_embedded} formula tag(s) in the PDF (a PDF 2.0 file, as "
                     "PDF/UA-2 allows), beside their read-aloud text.")
    if tv.formulas_read_from_mathml:
        lines.append(f"Formulas: {tv.formulas_read_from_mathml} of the document's own, which carry their MathML, given "
                     "read-aloud text from it by rule (MathCAT), with no AI.")
    if enr.math_formulas:
        read = sum(1 for f in enr.formulas if f.get("rule"))
        lines.append(f"Formulas: {enr.math_formulas} saved as MathML as well, in the math files and the formula list "
                     "(a web page a screen reader can move through part by part)"
                     + (f"; {read} also in Nemeth and UEB braille, and read by rule to check the AI's reading" if read
                        else "")
                     + (f": {enr.math_disagree} disagree and are flagged to choose between the two readings"
                        if enr.math_disagree else "") + ".")
        pictured = sum(1 for f in enr.formulas if f.get("picture"))
        if pictured:
            lines.append(f"Formulas read again from their pictures by the formula reader: {pictured}"
                         + (f", of which {enr.math_picture_disagree} say other numbers or letters than the AI's "
                            "transcription and are flagged to choose a reading" if enr.math_picture_disagree
                            else ", all agreeing with the AI's transcription") + ".")
    if enr.reading_order_pages:
        lines.append(f"Reading order: determined by AI on {enr.reading_order_pages} page(s).")
    lines.append(f"Document: title \"{title}\", language {language}, PDF/UA identifier set.")
    vf = comparison.get("visual_fidelity", {})
    lines.append(f"Visual fidelity of the non-destructive output: {vf.get('pages_identical')}/{vf.get('pages_compared')} pages pixel-identical.")
    if comparison.get("glyph_lists_removed"):
        lines.append(f"Font descriptors: incomplete glyph lists removed from {len(comparison['glyph_lists_removed'])} "
                     "font(s) (they're optional and unused for drawing; rendering unchanged).")
    if comparison.get("form_marks_redrawn"):
        lines.append(f"Form fields: the check marks of {comparison['form_marks_redrawn']} unchecked box(es) are now drawn as "
                     "shapes (they used ZapfDingbats, a font that isn't embedded); the pages look the same.")
    if comparison.get("unicode_maps_added"):
        lines.append(f"Unicode maps completed for {len(comparison['unicode_maps_added'])} font(s) "
                     f"({'; '.join(comparison['unicode_maps_added'][:4])}) so their text can be read aloud; rendering "
                     "unchanged.")
    if comparison.get("glyph_widths_modified_only"):
        lines.append(f"Font metrics: {comparison['glyph_widths_modified_only']} glyph width(s) corrected in the "
                     "modified file only (the shifts are a fraction of a point, invisible but not pixel-identical).")
    if tv_widths := comparison.get("glyph_widths_compensated"):
        lines.append(f"Font metrics: {tv_widths} glyph(s) whose declared width disagreed with the embedded font "
                     "corrected, with spacing compensated so the page renders identically.")
    fonts_embedded = comparison.get("fonts_embedded") or []
    if fonts_embedded:
        lines.append(f"Modified version: embedded metric-compatible substitutes for {len(fonts_embedded)} "
                     f"unembedded font(s) ({'; '.join(fonts_embedded[:6])}); tags unchanged.")
    if destructive_pages:
        lines.append(
            f"Destructive changes on page(s) {', '.join(str(p + 1) for p in destructive_pages)} "
            "(flattened to an image with an invisible, tagged text layer in an embedded font). Two files are returned: "
            "_compliant_modified.pdf (primary) and _compliant_visual.pdf (visually exact)."
        )
    remaining = after.get("verapdf", {})
    copy = comparison.get("after_pdfua2")
    for fl, v in remaining.items():
        clauses = v.get("failed_clauses") or []
        if clauses and fl == "ua2" and copy:
            # Both (#445): the PDF/UA-1 file can't be PDF/UA-2 (it's PDF 1.7); the copy made for that is judged apart
            lines.append(f"PDF/UA-2 of the PDF/UA-1 file, for information (clauses): {', '.join(clauses[:15])}; the "
                         f"PDF/UA-2 copy {'passes' if copy.get('compliant') else 'does not yet fully pass'}.")
        elif clauses:
            lines.append(f"Remaining {fl} failures (clauses): {', '.join(clauses[:15])}.")
    for w in warnings:
        lines.append(f"Warning: {w}")
    return "\n".join(lines)


