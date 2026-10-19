"""Non-destructive tagging of a whole document."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pikepdf
from pikepdf import Dictionary, Name

from app.pipeline.field_labels import Labels
from app.pipeline.field_labels import apply as apply_field_labels
from app.pipeline.models import BBox, DocumentAnalysis, bbox_intersection, bbox_union
from app.tagging.content_marker import ContentMarker, MarkResult, StreamMarks, count_form_uses, existing_mcids, mcid_of
from app.tagging.empty_spans import remove_empty_spans
from app.tagging.blank_fonts import fill_blank_fonts
from app.tagging.font_unicode import map_picture_glyphs
from app.tagging.existing_tags import DECORATIVE, OUTLINE, ElemInfo, ExistingTree
from app.tagging.legend import Legends
from app.tagging.mathml_tags import formula_mathml, has_mathml
from app.tagging.metadata import apply_document_metadata
from app.tagging.placement import place_added
from app.tagging.reorder import reorder_retained
from app.tagging.struct_tree import StructTreeBuilder
from app.tagging.targets import ARTIFACT_BACKGROUND, PageResolver

log = logging.getLogger(__name__)


@dataclass
class TagResult:
    existing_tags: dict
    tagged_units: int = 0
    artifacts: int = 0
    existing_units: int = 0
    stripped_marks: int = 0
    forms_wrapped: int = 0
    empty_spans_removed: int = 0  # kept tags: Spans around nothing visible, folded into their parent
    picture_glyphs_mapped: dict = field(default_factory=dict)  # font -> characters given private-use values
    picture_glyphs_in_text: list = field(default_factory=list)  # pages (1-based) where one stands in a sentence
    added_placed: int = 0  # kept tags: added tags put beside the kept tag they follow
    added_headings_demoted: int = 0  # ...and added "headings" made paragraphs (the document has its own outline)
    orphans_retagged: int = 0
    orphans_artifacted: int = 0  # untagged decorative pictures in the old tree, now artifacts
    artifacts_released: int = 0  # body text the old tagging hid as artifacts, now tagged
    containers_reordered: int = 0  # kept tags put in reading order
    hidden_text_artifacted: int = 0  # kept tags whose text is covered by shapes drawn over it
    legend_marks: int = 0  # marks given their legend's label as alt text
    elements: dict = field(default_factory=dict)
    figures_with_alt: int = 0
    figures_without_alt: int = 0
    figures_needing_alt: list = field(default_factory=list)  # (page, bbox) of existing figures with no alt
    # One per entry of figures_needing_alt: (page, bbox, the element's place in the saved file's tree walk, its place
    # in existing_tags["samples"] or None, whether it was left "retained"), for writing a description onto the
    # element afterwards without tagging the document again. Not part of the report.
    figure_handles: list = field(default_factory=list)
    figures_artifacted: int = 0
    readings_on_kept_tags: int = 0  # notation readings written as ActualText on tags the document arrived with
    readings_unplaced: int = 0  # ...and those no kept tag matched
    kept_notes: int = 0  # footnotes the document's own tags held as plain paragraphs, made Notes
    kept_note_refs: int = 0  # kept tags holding a footnote's mark (or a cell's table-note symbol) made to name the note
    figure_outlines_released: int = 0  # kept Figures with no description that were only lines around text
    # Kept Formulas that carry their own MathML (#403): {"page", "bbox", "mathml", "latex", "spoken", "rule"}, and
    # how many of them got their read-aloud text from it, read by rule (MathCAT), with no AI
    document_formulas: list = field(default_factory=list)
    formulas_read_from_mathml: int = 0
    artifact_mcids_resolved: int = 0
    tables: int = 0
    tables_linked: int = 0  # of them, complex tables whose cells name their headers
    footnotes: int = 0  # footnotes found and tagged as Note
    footnote_references: int = 0  # their marks in the text tagged as References of their own
    notes_given_ids: int = 0  # kept Notes that had no ID and were given one
    links: int = 0
    annotations: int = 0
    annotation_text_added: int = 0
    unmatched_paragraphs: int = 0
    passages_lang: int = 0
    fields_named: int = 0  # form fields given a tooltip from their label (#341)
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


PDF2_NS = "http://iso.org/pdf2/ssn"


def _document_in_pdf2_namespace(pdf: pikepdf.Pdf) -> bool:
    """The structure tree's one Document in the PDF 2.0 namespace (PDF/UA-2 8.2.5.2), where it isn't yet: the
    namespace is added to the tree's list. True if anything changed. The other tags stay in the default namespace,
    PDF 1.7's, which PDF 2.0 keeps."""
    root = pdf.Root.get("/StructTreeRoot")
    if not isinstance(root, pikepdf.Dictionary):
        return False
    kids = root.get("/K")
    kids = list(kids) if isinstance(kids, pikepdf.Array) else [kids] if kids is not None else []
    if len(kids) != 1 or not isinstance(kids[0], pikepdf.Dictionary) or kids[0].get("/S") != pikepdf.Name.Document:
        return False
    doc = kids[0]
    if str((doc.get("/NS") or {}).get("/NS", "")) == PDF2_NS:
        return False
    spaces = root.get("/Namespaces")
    if not isinstance(spaces, pikepdf.Array):
        root.Namespaces = spaces = pikepdf.Array()
    ns = next((n for n in spaces if isinstance(n, pikepdf.Dictionary) and str(n.get("/NS", "")) == PDF2_NS), None)
    if ns is None:
        ns = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Namespace, NS=pikepdf.String(PDF2_NS)))
        spaces.append(ns)
    doc.NS = ns
    return True


def _document_formulas(existing: ExistingTree, where, result: TagResult) -> dict:
    """The kept Formulas that carry their own MathML (#403), into result.document_formulas, each read by rule
    (MathCAT) for its speech and braille. Returns the readings for those with no read-aloud text yet, by element."""
    from app.pipeline import mathml, mathspeak
    found = []
    for e in existing.elements:
        if e.role != "Formula":
            continue
        raw, latex = formula_mathml(e.obj)
        clean = mathml.clean(raw, latex) if raw else None
        if not clean:
            continue
        at = where(e)
        said = str(e.obj.get("/Alt") or e.obj.get("/ActualText") or "").strip()
        found.append((e, clean, latex, at, said))
    answers = mathspeak.read_all([m for _, m, _, _, _ in found]) if found else []
    readings = {}
    for n, (e, clean, latex, at, said) in enumerate(found):
        a = answers[n] if n < len(answers) else {}
        rule = ({"spoken": mathspeak.speech_text(a["speech"]), "nemeth": a.get("nemeth"), "ueb": a.get("ueb")}
                if a.get("speech") else None)
        if rule and not said:
            readings[e.obj.objgen] = rule["spoken"]
        if at:
            result.document_formulas.append({"page": at[0], "bbox": list(at[1]), "source": "document",
                                             "mathml": clean, "latex": latex,
                                             "spoken": said or (rule or {}).get("spoken"),
                                             **({"rule": rule} if rule else {})})
    return readings


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
    field_labels: Labels | None = None,
    ignore_existing: bool = False,
) -> TagResult:
    """region_alts: extra alt text for existing Figure elements that matched no detected image
    (vector drawings, equations), keyed by page and region, from a previous pass. field_labels: what each form field
    is called (#341), set as its tooltip where it has none or only an internal name. ignore_existing: the person
    asked for the document's own tags to be ignored (they're poor): they're discarded and the tree built afresh,
    as when they can't be used (#396)."""
    layout_tables = layout_tables or set()
    with pikepdf.open(src) as pdf:
        fields_named = apply_field_labels(pdf, field_labels) if field_labels else 0
        existing = ExistingTree(pdf, _stream_mcids(pdf))
        report = existing.inspect()
        nested = _nested_mcid_pages(pdf) if report.present else []
        if report.usable and nested:
            # Tagged sections opened inside one another (common in Quartz "Save as PDF" output) are
            # invalid and cannot be kept; rebuilding strips every old marked section.
            report.usable, report.rebuilt_tree = False, True
            report.reason = (f"existing marked content is nested (one tagged section inside another) on "
                             f"page(s) {', '.join(str(p + 1) for p in nested[:10])}")
        if report.present and ignore_existing:
            report.usable, report.rebuilt_tree, report.reason = False, True, "ignored at your request"
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
        picture_glyphs: dict[tuple[int, int], set[int]] = {}
        body_glyphs: dict[tuple[int, int], set[int]] = {}
        body_pages: dict[tuple[int, int], set[int]] = {}
        glyph_counts: dict[tuple[int, int], list[int]] = {}
        # The streams as they are, for the pages that have to be marked a second time (below)
        originals = _original_streams(pdf, recursable) if retain else {}

        def mark(page_no: int, release: dict) -> MarkResult:
            pa = analysis.pages[page_no]
            resolvers[page_no] = PageResolver(pa, layout_tables)
            marker = ContentMarker(pdf, pdf.pages[page_no], pa.pdf_to_page, resolvers[page_no],
                                   strip_existing=not retain, recursable_forms=recursable, release_artifacts=release)
            return marker.run()

        results = [mark(page_no, {}) for page_no in range(len(pdf.pages))]
        if retain:
            # Body text a producer marked as an artifact (PowerPoint does this to text boxes) would be hidden from
            # screen readers. The pass just made says which artifact sections hold body text; the pages that have any
            # are put back as they were and marked again with those sections unwrapped, so their text is tagged.
            # Most documents have none, and are marked once.
            release = _body_artifacts(results)
            for page_no, mr in enumerate(results):
                owners = {sm.owner.objgen for sm in mr.streams}
                if owners & release.keys():
                    _restore_streams(pdf, pdf.pages[page_no], owners, originals)
                    results[page_no] = mark(page_no, release)
        for page_no, mr in enumerate(results):
            marks[page_no] = mr.streams
            result.tagged_units += mr.tagged_units
            result.artifacts += mr.artifacts
            result.existing_units += mr.existing_units
            result.stripped_marks += mr.stripped_marks
            result.forms_wrapped += mr.forms_wrapped
            result.artifacts_released += mr.released_artifacts
            for font, codes in mr.picture_glyphs.items():
                picture_glyphs.setdefault(font, set()).update(codes)
            for font, codes in mr.body_glyphs.items():
                body_glyphs.setdefault(font, set()).update(codes)
                body_pages.setdefault(font, set()).add(page_no)
            for font, (in_pictures, in_text) in mr.glyph_counts.items():
                total = glyph_counts.setdefault(font, [0, 0])
                total[0] += in_pictures
                total[1] += in_text

        if retain:
            page_index = {p.objgen: i for i, p in enumerate(pdf.pages)}
            existing_bbox = {sm.owner.objgen: sm.existing for ms in marks.values() for sm in ms}

            def elem_bbox(e: ElemInfo) -> tuple[int | None, BBox | None]:
                boxes = [existing_bbox.get(m.owner, {}).get(m.mcid) for m in e.mcrefs if m.valid]
                boxes = [b for b in boxes if b]
                pi = page_index.get(e.page) if e.page else None
                return pi, (bbox_union(boxes) if boxes else None)

            kinds_of = {sm.owner.objgen: sm.existing_kinds for ms in marks.values() for sm in ms}

            def is_outline(e: ElemInfo, bbox: BBox) -> bool:
                """Lines and shapes alone, drawn around text that has tags of its own: a text box's border, an
                arrow-shaped box holding a few words, the rules of a grid."""
                own = {(m.owner, m.mcid) for m in e.mcrefs if m.valid}
                drawn = [kinds_of.get(o, {}).get(n, set()) for o, n in own]
                if not drawn or not all(k and k <= {"path", "shading"} for k in drawn):
                    return False
                for mcid, box in existing_bbox.get(e.page, {}).items():
                    if (e.page, mcid) in own or not box or "text" not in kinds_of.get(e.page, {}).get(mcid, set()):
                        continue
                    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
                    if bbox[0] <= cx <= bbox[2] and bbox[1] <= cy <= bbox[3]:
                        return True
                return False

            def locate(e: ElemInfo) -> tuple[int, BBox] | None:
                """Where an element's content is, on the page its content is on (an element need not name its page:
                it can inherit it, as LaTeX's do)."""
                boxes, pi = [], None
                for d in existing._descendants(e):
                    for m in d.mcrefs:
                        b = existing_bbox.get(m.owner, {}).get(m.mcid) if m.valid else None
                        if b:
                            boxes.append(b)
                            pi = page_index.get(m.owner) if pi is None else pi
                return (pi, bbox_union(boxes)) if pi is not None and boxes else None

            legends = Legends(src)
            needing_alt: list[ElemInfo] = []  # the elements behind result.figures_needing_alt, in order

            def figure_alt(e: ElemInfo) -> str | None:
                carries_math = e.role == "Formula" and has_mathml(e.obj, e.children)
                if carries_math and math_readings.get(e.obj.objgen):
                    # Its own MathML, read by rule: the formula as its author wrote it, no AI needed (#403)
                    result.formulas_read_from_mathml += 1
                    return math_readings[e.obj.objgen]
                if not carries_math and e.children and not any(m.valid for m in e.mcrefs) and any(
                        "text" in kinds_of.get(m.owner, {}).get(m.mcid, set())
                        for d in existing._descendants(e) for m in d.mcrefs if m.valid):
                    # A Figure that draws nothing itself and only wraps tagged text (a group of text boxes): a
                    # grouping, not a picture
                    result.figure_outlines_released += 1
                    return OUTLINE
                pi, bbox = elem_bbox(e)
                if carries_math and (pi is None or bbox is None):
                    # A formula whose content sits in its MathML elements, or that names no page of its own
                    pi, bbox = locate(e) or (None, None)
                if pi is None or bbox is None:
                    return None
                w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
                if min(w, h) < 2 or (w < 6 and h < 6):
                    # A rule, a hairline or a dot tagged as a figure (Office does this) carries no information.
                    # Decided here: such a box has no area, so it can't be matched to a described region.
                    return DECORATIVE
                label = legends.label(pi, bbox)
                if label:
                    # A mark the page's legend explains ("▲ Beta"): its alt text is the legend's word, not an AI's
                    # "a blue triangle" (which an AI often calls decorative, dropping the meaning)
                    result.legend_marks += 1
                    return label
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
                if is_outline(e, bbox):
                    result.figure_outlines_released += 1
                    return OUTLINE
                result.figures_needing_alt.append((pi, bbox))
                needing_alt.append(e)
                return None

            def describe(e: ElemInfo):
                """Where an element and everything in it sits, and (for list items) the label printed with it."""
                boxes, pi = [], None
                for d in existing._descendants(e):
                    p, b = elem_bbox(d)
                    if b is not None:
                        boxes.append(b)
                        pi = p if pi is None else pi
                if pi is None or not boxes:
                    return None
                box = bbox_union(boxes)
                label = None
                if e.role == "LI":
                    best = max(analysis.pages[pi].blocks, key=lambda b: bbox_intersection(box, b.bbox), default=None)
                    if best is not None and bbox_intersection(box, best.bbox) > 0:
                        label = best.list_label
                return pi, box, label

            math_readings = _document_formulas(existing, locate, result)
            hidden = _hidden_text(marks)
            if hidden:
                result.hidden_text_artifacted = existing.hide(hidden, "text hidden under a shape drawn over it")
            try:
                referenced = existing.repair(figure_alt, describe)
            finally:
                legends.close()
            result.figure_handles = _figure_handles(existing, needing_alt, [(pi, b) for pi, b in result.figures_needing_alt])
            # "/Artifact <</MCID n>>" says both "skip me" and "I am tagged". Resolve each by whether the
            # tree uses it: referenced -> real content (drop the Artifact label), else -> artifact (drop the MCID).
            ambiguous_artifacts = _resolve_artifact_mcids(pdf, marks, referenced)
            result.artifact_mcids_resolved = ambiguous_artifacts
            to_artifact = existing.artifact_mcids()
            if to_artifact:
                result.figures_artifacted = _mark_as_artifacts(pdf, marks, to_artifact)
            # Orphaned existing MCIDs (from replaced elements or never referenced) are re-tagged. One that is a
            # decorative picture (a shape behind a slide's number) becomes an artifact; anything else the layout
            # can't place still gets a paragraph, so real text is never hidden.
            decorative_orphans: set = set()
            for page_no, ms in marks.items():
                for sm in ms:
                    keep = referenced.get(sm.owner.objgen, set())
                    for mcid, bbox in list(sm.existing.items()):
                        if mcid in keep or (sm.owner.objgen, mcid) in to_artifact:
                            continue
                        kinds = sm.existing_kinds.get(mcid, set())
                        drawing_only = bool(kinds) and kinds <= {"path", "shading"}
                        # Lines and shapes alone are placed as drawings are: in the figure they lie in, else
                        # decoration (a slide's tab shapes, a button's border)
                        key, _ = resolvers[page_no]("path" if drawing_only else "form", bbox, 0)
                        if key == ARTIFACT_BACKGROUND[0] or (drawing_only and key.startswith("artifact:")):
                            # Never an empty heading or paragraph
                            decorative_orphans.add((sm.owner.objgen, mcid))
                            continue
                        if key.startswith("artifact:"):
                            key = f"p{page_no}:unmatched"
                        sm.mcids.append((mcid, key))
                        result.orphans_retagged += 1
            if decorative_orphans:
                result.orphans_artifacted = _mark_as_artifacts(pdf, marks, decorative_orphans)
            _reading_order_findings(existing, elem_bbox, resolvers, analysis, report)
            # Readings of specialist notation go onto the kept tag that holds the same content
            placed, unplaced = _readings_onto_kept_tags(existing, elem_bbox, analysis)
            result.readings_on_kept_tags, result.readings_unplaced = placed, unplaced
            if unplaced:
                report.findings.append(
                    f"{unplaced} reading(s) of specialist notation could not be attached to the document's own tags "
                    "(no tag holds just that content, or the tag already says something of its own): those tags read "
                    "as they did" + (f"; {placed} were attached" if placed else ""))
            # Footnotes the analysis found, onto the kept tags that hold them and their marks
            kept_fn = _footnotes_onto_kept_tags(existing, elem_bbox, analysis)
            result.kept_notes, result.kept_note_refs = kept_fn["notes"], kept_fn["refs"]
            if kept_fn["notes"] or kept_fn["refs"]:
                report.findings.append(
                    f"footnotes on the document's own tags: {kept_fn['notes']} paragraph(s) made Notes, "
                    f"{kept_fn['refs']} tag(s) holding a mark made to name their note"
                    + (f", {kept_fn['said']} of them saying the mark as a footnote" if kept_fn["said"] else ""))
            # Spans around nothing visible (the tab after a form label) are folded into the tag they're in
            existing_kinds = {sm.owner.objgen: sm.existing_kinds for ms in marks.values() for sm in ms}
            result.empty_spans_removed = remove_empty_spans(pdf, src, existing_bbox, existing_kinds, existing.role_map)

        builder = StructTreeBuilder(pdf, analysis, layout_tables, language)
        root = builder.build(marks, retain_root=retain)
        if not retain:
            # A new tree's form fields go beside their labels, not after the page's text (#341)
            fields = [e for e in builder.inserted if str(e.get("/S", "")) == "/Form"]
            result.added_placed = place_added(pdf, analysis, marks, {}, fields)["placed"] if fields else 0
        if retain:
            # What the engine added (content the old tags left out, untagged fields and links) goes beside the kept
            # tags it follows on its page, not after everything
            placed = place_added(pdf, analysis, marks, existing.role_map, builder.inserted)
            result.added_placed, result.added_headings_demoted = placed["placed"], placed["headings_demoted"]
            # The kept tags' order is the producer's: put it in reading order where it clearly isn't
            result.containers_reordered = reorder_retained(pdf, analysis, marks, existing.role_map)
            if result.containers_reordered or placed["placed"]:
                for top in (root.K if isinstance(root.get("/K"), pikepdf.Array) else [root.get("/K")]):
                    if isinstance(top, Dictionary):
                        builder._normalize_headings(top)
        s = builder.stats
        result.elements = dict(s.elements)
        result.figures_with_alt, result.figures_without_alt = s.figures_with_alt, s.figures_without_alt
        result.tables, result.links, result.annotations = s.tables, s.links, s.annotations
        result.tables_linked = s.tables_linked
        result.footnotes, result.notes_given_ids = s.footnotes, s.notes_given_ids
        result.footnote_references = s.footnote_references
        result.annotation_text_added = s.contents_added
        result.unmatched_paragraphs = s.unmatched_paragraphs
        result.passages_lang = s.passages_lang
        result.fields_named = fields_named
        result.headings_renumbered = s.headings_renumbered

        # Characters in figures and decoration that have no Unicode meaning (note heads, drawn line pieces)
        result.picture_glyphs_mapped, stray = map_picture_glyphs(pdf, picture_glyphs, body_glyphs, glyph_counts)
        # Pages where such a symbol stands in a sentence: nothing says what it is, so a person should
        result.picture_glyphs_in_text = sorted({p + 1 for font in stray for p in body_pages.get(font, ())})
        # A PDF 2.0 file can only be PDF/UA-2 (#409): it's labelled as what it can be, and its Document is in the PDF
        # 2.0 namespace, as PDF/UA-2 asks (8.2.5.2)
        part = 2 if str(pdf.pdf_version) >= "2.0" else pdfua_part
        moved = part == 2 and _document_in_pdf2_namespace(pdf)
        result.metadata_changes = apply_document_metadata(pdf, title=title, language=language, pdfua_part=part)
        if moved:
            result.metadata_changes.append("Document tag put in the PDF 2.0 namespace")
        blank = fill_blank_fonts(pdf)
        if blank:
            result.metadata_changes.append(
                f"{len(blank)} font(s) of invisible text ({', '.join(sorted(set(blank))[:3])}) could only draw the "
                "missing glyph: each was given an empty glyph to draw (nothing on the page changes)")
        result.existing_tags = report.to_dict()
        if result.figure_handles:
            # Object numbers don't survive the save; the element's place in a walk of the final tree does
            place = {e.objgen: n for n, e in enumerate(_tree_walk(root)) if e.is_indirect}
            result.figure_handles = [(pi, bbox, place.get(og), si, kept) for pi, bbox, og, si, kept in result.figure_handles]
        pdf.save(dst)
    return result


def _tree_walk(root) -> list:
    """Every structure element under root, in the order a reader of the file meets them (each element before its
    children, children in order): the same walk on the same tree gives the same places, in memory or read back."""
    out, stack, seen = [], [], set()
    k = root.get("/K")
    for it in reversed(list(k) if isinstance(k, pikepdf.Array) else ([k] if k is not None else [])):
        stack.append(it)
    while stack:
        e = stack.pop()
        if not isinstance(e, Dictionary) or "/S" not in e or e.get("/Type") in (Name.MCR, Name.OBJR):
            continue
        if e.is_indirect:
            if e.objgen in seen:
                continue
            seen.add(e.objgen)
        out.append(e)
        k = e.get("/K")
        for it in reversed(list(k) if isinstance(k, pikepdf.Array) else ([k] if k is not None else [])):
            stack.append(it)
    return out


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
            mcid = mcid_of(props)
            if mcid is None:
                continue
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


NO_DESCRIPTION = "missing Alt (no description available)"
DESCRIPTION_ADDED = "missing Alt added"


def _figure_handles(existing: ExistingTree, elements: list[ElemInfo], regions: list) -> list:
    """For each existing figure left without a description: where its tag is in the saved file and in the report, so
    a description found afterwards can be written onto it without tagging the document again (see
    write_figure_alts)."""
    # The report's samples are the first 40 elements with a decision or a reason, in document order (ExistingTree.repair)
    sample_pos: dict[int, int] = {}
    for e in existing.elements:
        if (e.decision != "retained" or e.reasons) and e.decision != "removed":
            if len(sample_pos) < 40:
                sample_pos[id(e)] = len(sample_pos)
    return [(pi, bbox, e.obj.objgen, sample_pos.get(id(e)), e.decision == "retained")
            for (pi, bbox), e in zip(regions, elements)]


def write_figure_alts(path: Path, handles: list, alts: list[tuple[int, BBox, str]]) -> list:
    """Write descriptions found after tagging onto the figure tags the document arrived with, in place, and return
    the handles written. Each alt is matched to its handle by page and region. The same as tagging the document
    again with these descriptions, without the second pass: the tag gets its /Alt, as ExistingTree.repair would
    have given it."""
    wanted = {(pi, tuple(bbox)): alt for pi, bbox, alt in alts if alt}
    written = []
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        root = pdf.Root.get("/StructTreeRoot")
        elements = _tree_walk(root) if root is not None else []
        for handle in handles:
            pi, bbox, place = handle[0], tuple(handle[1]), handle[2]
            alt = wanted.get((pi, bbox))
            if alt is None or place is None or place >= len(elements):
                continue
            elem = elements[place]
            # An empty /Alt counts as none, as it did for the repair that left the figure undescribed
            if str(elem.get("/S", ""))[1:] not in ("Figure", "Formula") or str(elem.get("/Alt") or "").strip():
                continue
            elem.Alt = pikepdf.String(alt)
            written.append(handle)
        if written:
            pdf.save(path)
    return written


def apply_written_alts(result: TagResult, written: list) -> None:
    """Account in a TagResult for descriptions written with write_figure_alts, as a second tagging pass would have:
    the figure is repaired rather than left unresolved, and its sample line says so."""
    et = result.existing_tags
    samples = et.get("samples") or []
    for pi, bbox, _, sample_index, was_retained in written:
        if (pi, bbox) in result.figures_needing_alt:
            result.figures_needing_alt.remove((pi, bbox))
        et["unresolved"] = max(0, et.get("unresolved", 0) - 1)
        if was_retained:
            et["retained"] = max(0, et.get("retained", 0) - 1)
            et["repaired"] = et.get("repaired", 0) + 1
        if sample_index is not None and sample_index < len(samples):
            sample = samples[sample_index]
            sample["decision"] = "repaired"
            sample["reasons"] = [DESCRIPTION_ADDED if r == NO_DESCRIPTION else r for r in sample["reasons"]]
    result.figure_handles = [h for h in result.figure_handles if h not in written]


# Artifacts that really are page furniture keep their marking whatever text they hold
_FURNITURE_TYPES = {"Pagination"}
_FURNITURE_SUBTYPES = {"Header", "Footer", "Watermark", "PageNum", "Bates"}
BODY_TEXT_MIN = 60.0  # points of body text (about a dozen characters) before a section counts as content


def _body_artifacts(results: list[MarkResult]) -> dict[tuple[int, int], set[int]]:
    """Existing artifact sections that hold body text, by stream and sequence number, from a pass of the content
    marker that kept the artifacts as they were."""
    release: dict[tuple[int, int], set[int]] = {}
    for mr in results:
        for sm in mr.streams:
            for seq, info in sm.artifacts.items():
                if info["type"] in _FURNITURE_TYPES or info["subtype"] in _FURNITURE_SUBTYPES:
                    continue
                if info["body"] >= BODY_TEXT_MIN:
                    release.setdefault(sm.owner.objgen, set()).add(seq)
    return release


def _stream_bytes(obj) -> bytes:
    """The content of a page (one stream or several, read as one) or of a form, decoded."""
    contents = obj.get("/Contents") if not isinstance(obj, pikepdf.Stream) else obj
    if isinstance(contents, pikepdf.Stream):
        return contents.read_bytes()
    return b"\n".join(c.read_bytes() for c in (contents or []) if isinstance(c, pikepdf.Stream))


def _original_streams(pdf: pikepdf.Pdf, recursable: set) -> dict[tuple[int, int], bytes]:
    """Every page's content and every form the marker may tag from the inside, before the marker rewrites them."""
    originals = {page.objgen: _stream_bytes(page.obj) for page in pdf.pages}
    for og in recursable:
        try:
            form = pdf.get_object(og)
        except (ValueError, pikepdf.PdfError):
            continue
        if isinstance(form, pikepdf.Stream):
            originals[og] = _stream_bytes(form)
    return originals


def _restore_streams(pdf: pikepdf.Pdf, page: pikepdf.Page, owners: set, originals: dict) -> None:
    """Put a page's content, and the forms the marker tagged on it, back as they were before the marker ran."""
    page.obj.Contents = pdf.make_stream(originals[page.objgen])
    for og in owners:
        if og != page.objgen and og in originals:
            pdf.get_object(og).write(originals[og])


def _covers(fill: BBox, text: BBox) -> bool:
    """An opaque fill covers a piece of text: the middle of the text (its box less a fifth on each side, for glyph
    boxes that run past the ink) lies inside the fill."""
    w, h = text[2] - text[0], text[3] - text[1]
    x0, y0, x1, y1 = text[0] + 0.2 * w, text[1] + 0.2 * h, text[2] - 0.2 * w, text[3] - 0.2 * h
    return fill[0] - 1 <= x0 and fill[1] - 1 <= y0 and x1 <= fill[2] + 1 and y1 <= fill[3] + 1


def _hidden_text(marks: dict) -> set[tuple]:
    """Existing marked sections whose text no one can see: every piece of it is covered by an opaque shape or image
    drawn after it (PowerPoint leaves an old label under the shape of a new one). Text that's merely invisible
    (an OCR text layer) is real content and doesn't count."""
    hidden: set[tuple] = set()
    for streams in marks.values():
        fills = [f for sm in streams for f in sm.fills]
        if not fills:
            continue
        for sm in streams:
            verdict: dict[int, bool] = {}
            for z, mcid, bbox, visible in sm.text_units:
                if mcid is None:
                    continue
                covered = visible and any(fz > z and _covers(fb, bbox) for fz, fb in fills)
                verdict[mcid] = verdict.get(mcid, True) and covered
            for mcid, all_covered in verdict.items():
                if all_covered and sm.existing_kinds.get(mcid, {"text"}) <= {"text"}:
                    hidden.add((sm.owner.objgen, mcid))
    return hidden


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
                    and isinstance(ins.operands[1], Dictionary) and mcid_of(ins.operands[1]) in mcids:
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


_TEXT_ROLES = {"P", "LBody", "LI", "Lbl", "TD", "TH", "Span", "Caption", "Formula", "H", "H1", "H2", "H3", "H4", "H5",
               "H6", "Quote", "BlockQuote", "Note"}


def _footnotes_onto_kept_tags(existing, elem_bbox, analysis) -> dict:
    """When the document's own tags are kept, the footnotes the analysis found (structure._mark_footnotes and
    _mark_table_notes) would never reach them. The kept tag that holds a footnote becomes a Note, if it was a plain
    paragraph (one already a Note stays as it is; the builder gives Notes their IDs); the kept tag that holds the text
    with its mark, or a table cell with the note's symbol, names the note in /Ref, unless something in it already
    names one, and says the mark as a footnote ("soils (footnote 1)") when it has nothing of its own to say. Tags are
    matched by box, as readings are (_readings_onto_kept_tags). Marks aren't cut out as References of their own here:
    that would rewrite the producer's content. Returns the counts: notes, refs, said."""
    out = {"notes": 0, "refs": 0, "said": 0}

    def plain(e) -> bool:
        if e.role not in _TEXT_ROLES or e.objrefs or any(k in e.obj for k in ("/Alt", "/ActualText", "/E")):
            return False
        return all(c.role == "Span" and not c.children and not c.objrefs
                   and not any(k in c.obj for k in ("/Alt", "/ActualText", "/E")) for c in e.children)

    tags: dict[int, list] = {}
    for e in existing.elements:
        if e.decision in ("replaced", "removed", "artifacted") or e.role not in _TEXT_ROLES:
            continue
        boxes = [b for b in (elem_bbox(d) for d in existing._descendants(e)) if b[1] is not None]
        if boxes and len({pi for pi, _ in boxes}) == 1:
            tags.setdefault(boxes[0][0], []).append((e, bbox_union([b for _, b in boxes])))

    def best(pi: int, box: BBox, ok, inside: bool = False) -> object:
        """The tag whose box and this one cover each other; inside: whose box lies in this one (a table cell's box
        is its whole cell, the tag's only its glyphs), the largest such."""
        found, score = None, 0.0
        for e, eb in tags.get(pi, []):
            if not ok(e):
                continue
            inter = bbox_intersection(box, eb)
            if inside:
                s = _area(eb) if inter >= 0.8 * (_area(eb) or 1.0) else 0.0
            else:
                union = _area(box) + _area(eb) - inter
                s = inter / union if union > 0 else 0.0
            if s > score:  # of two that fit alike, the first (the outer one) holds all of it
                found, score = e, s
        return found if (score > 0 if inside else score >= 0.6) else None

    for page in analysis.pages:
        notes: dict[str, object] = {}
        for b in page.blocks:
            if b.role != "Note":
                continue
            e = best(page.number, b.bbox, lambda e: e.role in ("Note", "P"))
            if e is None:
                continue
            if e.role == "P":
                if not plain(e):
                    continue
                e.obj.S = Name.Note
                e.role = e.raw_role = "Note"
                existing._mark(e, "repaired", "footnote held as a paragraph: tagged as a Note")
                out["notes"] += 1
            notes[b.id] = e
        if not notes:
            continue
        held = set(id(e) for e in notes.values())

        def point(e, targets: list, said: str | None) -> None:
            if any("/Ref" in d.obj for d in existing._descendants(e)):
                return  # the producer already says what it points at
            e.obj.Ref = pikepdf.Array([t.obj for t in targets])
            out["refs"] += 1
            note = "names its footnote in /Ref"
            if said and plain(e):
                e.obj.ActualText = pikepdf.String(said)
                out["said"] += 1
                note += ", and says the mark as a footnote"
            existing._mark(e, "repaired", note)

        for b in page.blocks:
            targets = [notes[n] for n in b.notes if n in notes]
            if not targets or b.role == "Note":
                continue
            e = best(page.number, b.bbox, lambda e: id(e) not in held and e.role != "Note")
            if e is not None:
                point(e, targets, b.actual_text if b.says_marks_only else None)
        for t in page.tables:
            for c in t.cells:
                targets = [notes[n] for sym, n in t.notes.items() if sym in (c.text or "") and n in notes]
                if targets and c.bbox:
                    e = best(page.number, c.bbox, lambda e: e.role in ("TD", "TH"), inside=True)
                    if e is not None:
                        point(e, targets, c.actual_text)
    return out


def _readings_onto_kept_tags(existing, elem_bbox, analysis) -> tuple[int, int]:
    """A reading belongs to a block of the analysis; when the document's own tags are kept, the tag tree isn't
    built from the analysis and the reading would never reach the file. It is written as ActualText on the kept
    tag whose content is that block's: the two must cover each other, the tag must be a text tag with nothing of
    its own to say (no Alt, ActualText or expansion), and anything inside it must be plain Spans. Returns how many
    readings were written, and how many found no such tag."""
    from app.pipeline.notation import has_doses

    wanted: dict[int, list[tuple[BBox, str]]] = {}
    for page in analysis.pages:
        for b in page.blocks:
            if b.notation_reading and b.actual_text and b.role != "Artifact":
                wanted.setdefault(page.number, []).append((b.bbox, b.actual_text))
        for table in page.tables:
            for c in table.cells:
                if c.actual_text and c.bbox and has_doses(c.text or ""):
                    wanted.setdefault(page.number, []).append((c.bbox, c.actual_text))
    if not wanted:
        return 0, 0

    def plain(e) -> bool:
        if e.role not in _TEXT_ROLES or e.objrefs or any(k in e.obj for k in ("/Alt", "/ActualText", "/E")):
            return False
        return all(c.role == "Span" and not c.children and not c.objrefs
                   and not any(k in c.obj for k in ("/Alt", "/ActualText", "/E")) for c in e.children)

    candidates: dict[int, list] = {}
    for e in existing.elements:
        if e.decision in ("replaced", "removed", "artifacted") or not plain(e):
            continue
        boxes = []
        for d in existing._descendants(e):
            pi, bbox = elem_bbox(d)
            if bbox is not None:
                boxes.append((pi, bbox))
        if boxes and len({pi for pi, _ in boxes}) == 1:
            candidates.setdefault(boxes[0][0], []).append((e, bbox_union([b for _, b in boxes])))
    # What the kept tags hold, text tag or not: a reading whose content no kept tag holds isn't lost, because that
    # content is tagged afresh from the analysis, reading and all
    held: dict[int, list[BBox]] = {}
    for e in existing.leaf_elements():
        pi, bbox = elem_bbox(e)
        if pi is not None and bbox is not None:
            held.setdefault(pi, []).append(bbox)
    placed = unplaced = 0
    used: set[int] = set()
    for pi, readings in wanted.items():
        for bbox, said in readings:
            best, best_score = None, 0.0
            for e, eb in candidates.get(pi, []):
                if id(e) in used:
                    continue
                inter = bbox_intersection(bbox, eb)
                union = _area(bbox) + _area(eb) - inter
                score = inter / union if union > 0 else 0.0
                if score > 0 and score >= best_score:  # of two that fit alike, the one further inside
                    best, best_score = e, score
            if best is not None and best_score >= 0.6:
                best.obj.ActualText = pikepdf.String(said)
                existing._mark(best, "repaired", "reading of specialist notation written as ActualText")
                used.add(id(best))
                placed += 1
            elif any(bbox_intersection(bbox, hb) >= 0.3 * (_area(bbox) or 1.0) for hb in held.get(pi, [])):
                unplaced += 1
    return placed, unplaced


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
