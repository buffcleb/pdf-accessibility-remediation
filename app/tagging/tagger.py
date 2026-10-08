"""Non-destructive tagging of a whole document."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pikepdf
from pikepdf import Dictionary, Name

from app.pipeline.models import BBox, DocumentAnalysis, bbox_intersection, bbox_union
from app.tagging.content_marker import ContentMarker, StreamMarks, count_form_uses, existing_mcids
from app.tagging.existing_tags import DECORATIVE, ElemInfo, ExistingTree
from app.tagging.metadata import apply_document_metadata
from app.tagging.struct_tree import StructTreeBuilder
from app.tagging.targets import PageResolver

log = logging.getLogger(__name__)


@dataclass
class TagResult:
    existing_tags: dict
    tagged_units: int = 0
    artifacts: int = 0
    existing_units: int = 0
    stripped_marks: int = 0
    forms_wrapped: int = 0
    orphans_retagged: int = 0
    elements: dict = field(default_factory=dict)
    figures_with_alt: int = 0
    figures_without_alt: int = 0
    figures_needing_alt: list = field(default_factory=list)  # (page, bbox) of existing figures with no alt
    figures_artifacted: int = 0
    artifact_mcids_resolved: int = 0
    tables: int = 0
    links: int = 0
    annotations: int = 0
    annotation_text_added: int = 0
    unmatched_paragraphs: int = 0
    headings_renumbered: int = 0
    metadata_changes: list[str] = field(default_factory=list)


def _clear_stale_struct_keys(pdf: pikepdf.Pdf) -> int:
    """Remove /StructParents from pages and form XObjects and /StructParent from annotations."""
    cleared, seen = 0, set()

    def forms(res):
        xobjs = res.get("/XObject") if isinstance(res, Dictionary) else None
        for _, x in (xobjs.items() if isinstance(xobjs, Dictionary) else []):
            if isinstance(x, pikepdf.Stream) and x.get("/Subtype") == Name.Form and x.objgen not in seen:
                seen.add(x.objgen)
                yield x
                yield from forms(x.get("/Resources"))

    for page in pdf.pages:
        for obj in [page.obj, *forms(page.obj.get("/Resources"))]:
            if "/StructParents" in obj:
                del obj["/StructParents"]
                cleared += 1
        for annot in page.obj.get("/Annots") or []:
            if isinstance(annot, Dictionary) and "/StructParent" in annot:
                del annot["/StructParent"]
                cleared += 1
    return cleared


def _nested_mcid_pages(pdf: pikepdf.Pdf) -> list[int]:
    """Pages whose content opens an MCID section inside another MCID section."""
    pages = []
    for i, page in enumerate(pdf.pages):
        try:
            ops = pikepdf.parse_content_stream(page.obj)
        except pikepdf.PdfError:
            continue
        stack: list[bool] = []
        for ins in ops:
            if not isinstance(ins, pikepdf.ContentStreamInstruction):
                continue
            op = str(ins.operator)
            if op in ("BDC", "BMC"):
                has_mcid = (op == "BDC" and len(ins.operands) == 2 and isinstance(ins.operands[1], Dictionary)
                            and "/MCID" in ins.operands[1])
                if has_mcid and any(stack):
                    pages.append(i)
                    break
                stack.append(has_mcid)
            elif op == "EMC" and stack:
                stack.pop()
    return pages


def _stream_mcids(pdf: pikepdf.Pdf) -> dict[tuple[int, int], set[int]]:
    found: dict[tuple[int, int], set[int]] = {}
    for page in pdf.pages:
        try:
            found[page.objgen] = existing_mcids(pikepdf.parse_content_stream(page.obj))
        except pikepdf.PdfError:
            found[page.objgen] = set()
        res = page.obj.get("/Resources")
        xobjs = res.get("/XObject") if isinstance(res, Dictionary) else None
        for _, x in (xobjs.items() if isinstance(xobjs, Dictionary) else []):
            if isinstance(x, pikepdf.Stream) and x.get("/Subtype") == Name.Form and "/StructParents" in x:
                try:
                    found[x.objgen] = existing_mcids(pikepdf.parse_content_stream(x))
                except pikepdf.PdfError:
                    pass
    return found


def tag_document(
    src: Path,
    dst: Path,
    analysis: DocumentAnalysis,
    *,
    title: str,
    language: str,
    pdfua_part: int = 1,
    layout_tables: set[str] | None = None,
    region_alts: list[tuple[int, BBox, str]] | None = None,
) -> TagResult:
    """region_alts: extra alt text for existing Figure elements that matched no detected image
    (vector drawings, equations), keyed by page and region, from a previous pass."""
    layout_tables = layout_tables or set()
    with pikepdf.open(src) as pdf:
        existing = ExistingTree(pdf, _stream_mcids(pdf))
        report = existing.inspect()
        nested = _nested_mcid_pages(pdf) if report.present else []
        if report.usable and nested:
            # Tagged sections opened inside one another (common in Quartz "Save as PDF" output) are
            # invalid and cannot be kept; rebuilding strips every old marked section.
            report.usable, report.rebuilt_tree = False, True
            report.reason = (f"existing marked content is nested (one tagged section inside another) on "
                             f"page(s) {', '.join(str(p + 1) for p in nested[:10])}")
        retain = report.usable
        if report.present and not retain:
            existing.strip()
            log.info("existing structure tree discarded: %s", report.reason)
        if not retain:
            # Leftover /StructParents keys (e.g. Google/Office exports with no real tree) would collide
            # with the keys the new tree assigns, and stop forms from being tagged inside.
            result_stale = _clear_stale_struct_keys(pdf)
            if result_stale:
                log.info("cleared %d stale StructParents key(s)", result_stale)

        recursable = {og for og, n in count_form_uses(pdf).items() if n == 1}
        resolvers: dict[int, PageResolver] = {}
        marks: dict[int, list[StreamMarks]] = {}
        result = TagResult(existing_tags={})
        for page_no, page in enumerate(pdf.pages):
            pa = analysis.pages[page_no]
            resolvers[page_no] = PageResolver(pa, layout_tables)
            marker = ContentMarker(pdf, page, pa.pdf_to_page, resolvers[page_no],
                                   strip_existing=not retain, recursable_forms=recursable)
            mr = marker.run()
            marks[page_no] = mr.streams
            result.tagged_units += mr.tagged_units
            result.artifacts += mr.artifacts
            result.existing_units += mr.existing_units
            result.stripped_marks += mr.stripped_marks
            result.forms_wrapped += mr.forms_wrapped

        if retain:
            page_index = {p.objgen: i for i, p in enumerate(pdf.pages)}
            existing_bbox = {sm.owner.objgen: sm.existing for ms in marks.values() for sm in ms}

            def elem_bbox(e: ElemInfo) -> tuple[int | None, BBox | None]:
                boxes = [existing_bbox.get(m.owner, {}).get(m.mcid) for m in e.mcrefs if m.valid]
                boxes = [b for b in boxes if b]
                pi = page_index.get(e.page) if e.page else None
                return pi, (bbox_union(boxes) if boxes else None)

            def figure_alt(e: ElemInfo) -> str | None:
                pi, bbox = elem_bbox(e)
                if pi is None or bbox is None:
                    return None
                best, best_ov = None, 0.0
                for img in analysis.pages[pi].images:
                    ov = bbox_intersection(bbox, img.bbox)
                    if ov > best_ov and img.alt:
                        best, best_ov = img, ov
                if best:
                    return best.alt
                for rp, rb, alt, *rest in region_alts or []:
                    # Same region, not merely overlapping: a tiny mark inside a big figure must not match it.
                    # Intersection over union tolerates the small shifts between the visual and modified
                    # files (corrected glyph widths) while rejecting a mark inside a larger figure.
                    inter = bbox_intersection(bbox, rb)
                    union = _area(bbox) + _area(rb) - inter
                    if rp == pi and union > 0 and inter / union >= 0.6:
                        decorative = bool(rest and rest[0])
                        return DECORATIVE if decorative and not alt else alt
                result.figures_needing_alt.append((pi, bbox))
                return None

            referenced = existing.repair(figure_alt)
            # "/Artifact <</MCID n>>" says both "skip me" and "I am tagged". Resolve each by whether the
            # tree uses it: referenced -> real content (drop the Artifact label), else -> artifact (drop the MCID).
            ambiguous_artifacts = _resolve_artifact_mcids(pdf, marks, referenced)
            result.artifact_mcids_resolved = ambiguous_artifacts
            to_artifact = existing.artifact_mcids()
            if to_artifact:
                result.figures_artifacted = _mark_as_artifacts(pdf, marks, to_artifact)
            # Orphaned existing MCIDs (from replaced elements or never referenced) are re-tagged.
            for page_no, ms in marks.items():
                for sm in ms:
                    keep = referenced.get(sm.owner.objgen, set())
                    for mcid, bbox in sm.existing.items():
                        if mcid in keep or (sm.owner.objgen, mcid) in to_artifact:
                            continue
                        key, _ = resolvers[page_no]("form", bbox, 0)
                        if key.startswith("artifact:"):
                            key = f"p{page_no}:unmatched"
                        sm.mcids.append((mcid, key))
                        result.orphans_retagged += 1
            _reading_order_findings(existing, elem_bbox, resolvers, analysis, report)

        builder = StructTreeBuilder(pdf, analysis, layout_tables)
        builder.build(marks, retain_root=retain)
        s = builder.stats
        result.elements = dict(s.elements)
        result.figures_with_alt, result.figures_without_alt = s.figures_with_alt, s.figures_without_alt
        result.tables, result.links, result.annotations = s.tables, s.links, s.annotations
        result.annotation_text_added = s.contents_added
        result.unmatched_paragraphs = s.unmatched_paragraphs
        result.headings_renumbered = s.headings_renumbered

        result.metadata_changes = apply_document_metadata(pdf, title=title, language=language, pdfua_part=pdfua_part)
        result.existing_tags = report.to_dict()
        pdf.save(dst)
    return result


def _resolve_artifact_mcids(pdf: pikepdf.Pdf, marks: dict, referenced: dict) -> int:
    from pikepdf import ContentStreamInstruction, Operator

    fixed = 0
    owners = {sm.owner.objgen: sm for ms in marks.values() for sm in ms}
    for og, sm in owners.items():
        ops = list(pikepdf.parse_content_stream(sm.owner))
        keep = referenced.get(og, set())
        changed = False
        for i, ins in enumerate(ops):
            if not (isinstance(ins, ContentStreamInstruction) and str(ins.operator) == "BDC"):
                continue
            tag, props = ins.operands[0], ins.operands[1] if len(ins.operands) > 1 else None
            if tag != Name.Artifact or not isinstance(props, Dictionary) or "/MCID" not in props:
                continue
            mcid = int(props.MCID)
            if mcid in keep:
                ops[i] = ContentStreamInstruction([Name.Span, Dictionary(MCID=mcid)], Operator("BDC"))
            else:
                clean = Dictionary({k: v for k, v in props.items() if k != "/MCID"})
                ops[i] = ContentStreamInstruction([Name.Artifact, clean], Operator("BDC"))
                sm.existing.pop(mcid, None)  # an artifact now, so not an orphan to re-tag
            changed = True
            fixed += 1
        if changed:
            data = pikepdf.unparse_content_stream(ops)
            if isinstance(sm.owner, pikepdf.Stream):
                sm.owner.write(data)
            else:
                sm.owner.Contents = pdf.make_stream(data)
    return fixed


def _mark_as_artifacts(pdf: pikepdf.Pdf, marks: dict, targets: set) -> int:
    """Re-mark the content of decorative figures as /Artifact (the MCID goes away with the tag)."""
    from pikepdf import ContentStreamInstruction, Operator

    done = 0
    owners = {sm.owner.objgen: sm.owner for ms in marks.values() for sm in ms}
    for og, owner in owners.items():
        mcids = {m for o, m in targets if o == og}
        if not mcids:
            continue
        ops = list(pikepdf.parse_content_stream(owner))
        changed = False
        for i, ins in enumerate(ops):
            if isinstance(ins, ContentStreamInstruction) and str(ins.operator) == "BDC" \
                    and isinstance(ins.operands[1], Dictionary) and int(ins.operands[1].get("/MCID", -1)) in mcids:
                ops[i] = ContentStreamInstruction([Name.Artifact, Dictionary(Type=Name.Layout)], Operator("BDC"))
                changed = True
                done += 1
        if changed:
            data = pikepdf.unparse_content_stream(ops)
            if isinstance(owner, pikepdf.Stream):
                owner.write(data)
            else:
                owner.Contents = pdf.make_stream(data)
        for ms in marks.values():
            for sm in ms:
                if sm.owner.objgen == og:
                    for m in mcids:
                        sm.existing.pop(m, None)
    return done


def _area(b: BBox) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _reading_order_findings(existing, elem_bbox, resolvers, analysis, report) -> None:
    """Compare the retained tree's order against the detected layout order, page by page."""
    per_page: dict[int, list[int]] = {}
    for e in existing.leaf_elements():
        pi, bbox = elem_bbox(e)
        if pi is None or bbox is None:
            continue
        key, _ = resolvers[pi]("form", bbox, 0)
        order = analysis.pages[pi].reading_order
        base = key.split(":")[0]
        if base in order:
            per_page.setdefault(pi, []).append(order.index(base))
    for pi, seq in per_page.items():
        if len(seq) < 4:
            continue
        pairs = list(zip(seq, seq[1:]))
        agree = sum(1 for a, b in pairs if a <= b) / len(pairs)
        if agree < 0.6:
            report.findings.append(
                f"page {pi + 1}: existing tag order agrees with detected reading order for only {agree:.0%} of "
                "adjacent elements; review reading order"
            )
