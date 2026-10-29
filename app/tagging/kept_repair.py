"""The kept-tags route of tagging (engine refactor plan, item 3): when a document's own tags are usable, after its
pages are marked and before the tree is built, its tags are repaired in place and what the layout found is put onto
them. The steps run in this order on purpose: text hidden under shapes and drawn off the page is set aside before
the tags are repaired; ambiguous artifact MCIDs are resolved before orphaned content is re-tagged; readings,
footnotes, empty spans and link words come last, on the repaired tree.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import pikepdf
from pikepdf import Dictionary, Name

from app.pipeline.models import BBox, DocumentAnalysis, bbox_area, bbox_intersection, bbox_union
from app.tagging.content_marker import StreamMarks, mcid_of, write_ops
from app.tagging.empty_spans import remove_empty_spans
from app.tagging.existing_tags import DECORATIVE, OUTLINE, ElemInfo, ExistingTree, ExistingTagReport
from app.tagging.legend import Legends
from app.tagging.link_words import move_link_words
from app.tagging.mathml_tags import formula_mathml, has_mathml, in_mathml, mathml_from_elements
from app.tagging.targets import ARTIFACT_BACKGROUND, PageResolver
from app.tagging.tree import TEXT_ROLES as _TEXT_ROLES

if TYPE_CHECKING:
    from app.tagging.tagger import TagResult

log = logging.getLogger(__name__)


def repair_kept(pdf: pikepdf.Pdf, src: Path, analysis: DocumentAnalysis, existing: ExistingTree,
                report: ExistingTagReport, marks: dict[int, list[StreamMarks]], resolvers: dict[int, PageResolver],
                result: TagResult, region_alts: list | None) -> dict:
    """Repair the document's own tags in place (marks: each page's marked content, as the marking pass left it;
    result: the TagResult the counts go into). Returns where the kept figures and formulas are, for their bounding
    box (#437)."""
    kept_boxes: dict = {}
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
        for d in existing.descendants(e):
            for m in d.mcrefs:
                b = existing_bbox.get(m.owner, {}).get(m.mcid) if m.valid else None
                if b:
                    boxes.append(b)
                    pi = page_index.get(m.owner) if pi is None else pi
        return (pi, bbox_union(boxes)) if pi is not None and boxes else None

    # The document's own MathML, read by rule (#403): figure_alt gives it to the formulas that carry it
    math_readings = _document_formulas(existing, locate, result, src)
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
                for d in existing.descendants(e) for m in d.mcrefs if m.valid):
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
            union = bbox_area(bbox) + bbox_area(rb) - inter
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
        for d in existing.descendants(e):
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

    hidden = _hidden_text(marks)
    if hidden:
        result.hidden_text_artifacted = existing.hide(hidden, "text hidden under a shape drawn over it")
    # Kept tags on content drawn wholly outside the page's visible area: no one sees it, and PAC flags it as
    # content in a possibly inadmissible location (#438)
    off = _off_page(marks, {pi: (p.width, p.height) for pi, p in enumerate(analysis.pages)})
    if off:
        result.off_page_artifacted = existing.hide(off, "content drawn outside the page's visible area")
    # A grouping tag holding nothing but lines and shapes (a bar down the page's edge) is decoration (#438),
    # unless it's described: Word's pictures are mapped to Sect, and a described one is a figure (#486)
    for e in existing.elements:
        described = str(e.obj.get("/Alt") or "").strip() or str(e.obj.get("/ActualText") or "").strip()
        if (e.role in ("Art", "Sect", "Div", "Part") and not e.children and not e.objrefs and e.mcrefs
                and all(m.valid for m in e.mcrefs) and not described):
            kinds = [kinds_of.get(m.owner, {}).get(m.mcid, set()) for m in e.mcrefs]
            if all(k and k <= {"path", "shading"} for k in kinds):
                existing.mark(e, "artifacted", "a grouping tag holding only lines or shapes: decoration")
                result.groupings_artifacted += 1
    # Runs of only spaces in tags with no visible text of their own (Word's blank lines) become artifacts (#490)
    blank = {(sm.owner.objgen, mcid) for ms in marks.values() for sm in ms
             for mcid, kinds in sm.existing_kinds.items() if kinds == {"text"} and mcid not in sm.existing_ink}
    result.blank_runs_artifacted = existing.hide_blank(blank) if blank else 0
    if result.blank_runs_artifacted:
        existing.report.findings.append(
            f"{result.blank_runs_artifacted} run(s) of only spaces in tags with nothing else to read (blank "
            "lines kept as paragraphs) made artifacts")
    try:
        referenced = existing.repair(figure_alt, describe)
    finally:
        legends.close()
    # Where the kept figures and formulas are, for their bounding box (#437)
    kept_boxes = {e.obj.objgen: elem_bbox(e) for e in existing.elements
                  if e.role in ("Figure", "Formula") and e.obj.is_indirect}
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
                if kinds == {"text"} and mcid not in sm.existing_ink:
                    # Only spaces (Word's blank lines, as in #490): never a paragraph of its own
                    decorative_orphans.add((sm.owner.objgen, mcid))
                    continue
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
    result.empty_spans_removed = remove_empty_spans(pdf, src, existing_bbox, kinds_of, existing.role_map)
    # Link tags holding only their annotation take the words inside the link's box (#489)
    matrices = {pdf.pages[i].objgen: p.pdf_to_page for i, p in enumerate(analysis.pages) if i < len(pdf.pages)}
    result.links_given_words = move_link_words(pdf, existing_bbox, kinds_of, matrices, existing.role_map)
    if result.links_given_words:
        report.findings.append(f"{result.links_given_words} link tag(s) given the words inside the link")

    # The report's counts and samples, after the last step that marks a kept tag (#622); the figure handles name
    # their place among those samples
    existing.finish_report()
    result.figure_handles = _figure_handles(existing, needing_alt, [(pi, b) for pi, b in result.figures_needing_alt])
    return kept_boxes


def _mcid_texts(src: Path, pages: set[int]) -> dict[tuple[int, int], str]:
    """The text of each marked-content item on these pages, by (page, MCID): read by pdfplumber with the checker's
    marked-content tracking (app.verification.semantic). Content in forms isn't included."""
    import pdfplumber

    import app.verification.semantic  # noqa: F401 - gives pdfplumber's glyphs their MCID
    out: dict[tuple[int, int], list[str]] = {}
    with pdfplumber.open(src) as doc:
        for i in sorted(pages):
            if i < len(doc.pages):
                for c in doc.pages[i].chars:
                    if c.get("mcid") is not None and not c.get("mcid_forms"):
                        out.setdefault((i, c["mcid"]), []).append(c["text"])
    return {k: "".join(v).strip() for k, v in out.items()}


def _document_formulas(existing: ExistingTree, where, result: TagResult, src: Path | None = None) -> dict:
    """The kept Formulas that carry their own MathML (#403), into result.document_formulas, each read by rule
    (MathCAT) for its speech and braille: an associated MathML file, or MathML structure elements under the Formula,
    rebuilt with their text from the page (#421). Returns the readings for those with no read-aloud text yet, by
    element."""
    from app.pipeline import mathml, mathspeak
    page_of = {p.objgen: i for i, p in enumerate(existing.pdf.pages)}
    texts: dict | None = None

    def text_of(page, mcid: int) -> str:
        nonlocal texts
        i = page_of.get(page.objgen) if isinstance(page, pikepdf.Object) else None
        if i is None or src is None:
            return ""
        if texts is None:  # read once, the pages with such formulas only
            pages = {page_of.get(d.page) for e in existing.elements if e.role == "Formula" and not formula_mathml(e.obj)[0]
                     for d in existing.descendants(e) if d.page is not None}
            texts = _mcid_texts(src, {p for p in pages if p is not None})
        return texts.get((i, mcid), "")

    found = []
    for e in existing.elements:
        if e.role != "Formula":
            continue
        raw, latex = formula_mathml(e.obj)
        if raw is None and any(in_mathml(c.obj) for c in e.children):
            raw = mathml_from_elements(e.obj, text_of)
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
            write_ops(pdf, sm.owner, ops)
    return fixed


def _figure_handles(existing: ExistingTree, elements: list[ElemInfo], regions: list) -> list:
    """For each existing figure left without a description: where its tag is in the saved file and in the report, so
    a description found afterwards can be written onto it without tagging the document again (see
    write_figure_alts)."""
    # The report's samples are the first 40 elements with a decision or a reason, in document order
    # (ExistingTree.finish_report)
    sample_pos: dict[int, int] = {}
    for e in existing.elements:
        if (e.decision != "retained" or e.reasons) and e.decision != "removed":
            if len(sample_pos) < 40:
                sample_pos[id(e)] = len(sample_pos)
    return [(pi, bbox, e.obj.objgen, sample_pos.get(id(e)), e.decision == "retained")
            for (pi, bbox), e in zip(regions, elements)]


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


def _off_page(marks: dict, sizes: dict) -> set[tuple]:
    """Existing marked sections drawn wholly outside their page's visible area (sizes: page index -> width and
    height, the layout's page space the boxes are in)."""
    out: set[tuple] = set()
    for pi, streams in marks.items():
        w, h = sizes.get(pi, (0, 0))
        if not w or not h:
            continue
        for sm in streams:
            for mcid, b in sm.existing.items():
                if b[2] <= 0 or b[0] >= w or b[3] <= 0 or b[1] >= h:
                    out.add((sm.owner.objgen, mcid))
    return out


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
            write_ops(pdf, owner, ops)
        for ms in marks.values():
            for sm in ms:
                if sm.owner.objgen == og:
                    for m in mcids:
                        sm.existing.pop(m, None)
    return done


def _plain(e) -> bool:
    """A text tag with nothing written on it (no alt text, reading or expansion) and only bare Spans inside: one a
    note or a reading found in its text can be written onto."""
    if e.role not in _TEXT_ROLES or e.objrefs or any(k in e.obj for k in ("/Alt", "/ActualText", "/E")):
        return False
    return all(c.role == "Span" and not c.children and not c.objrefs
               and not any(k in c.obj for k in ("/Alt", "/ActualText", "/E")) for c in e.children)


def _footnotes_onto_kept_tags(existing, elem_bbox, analysis) -> dict:
    """When the document's own tags are kept, the footnotes the analysis found (structure.notes.mark_footnotes and
    mark_table_notes) would never reach them. The kept tag that holds a footnote becomes a Note, if it was a plain
    paragraph (one already a Note stays as it is; the builder gives Notes their IDs); the kept tag that holds the text
    with its mark, or a table cell with the note's symbol, names the note in /Ref, unless something in it already
    names one, and says the mark as a footnote ("soils (footnote 1)") when it has nothing of its own to say. Tags are
    matched by box, as readings are (_readings_onto_kept_tags). Marks aren't cut out as References of their own here:
    that would rewrite the producer's content. Returns the counts: notes, refs, said."""
    out = {"notes": 0, "refs": 0, "said": 0}

    tags: dict[int, list] = {}
    for e in existing.elements:
        if e.decision in ("replaced", "removed", "artifacted") or e.role not in _TEXT_ROLES:
            continue
        boxes = [b for b in (elem_bbox(d) for d in existing.descendants(e)) if b[1] is not None]
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
                s = bbox_area(eb) if inter >= 0.8 * (bbox_area(eb) or 1.0) else 0.0
            else:
                union = bbox_area(box) + bbox_area(eb) - inter
                s = inter / union if union > 0 else 0.0
            if s > score:  # of two that fit alike, the first (the outer one) holds all of it
                found, score = e, s
        return found if (score > 0 if inside else score >= 0.6) else None

    by_obj = {e.obj.objgen: e for e in existing.elements}

    def in_note(e) -> object:
        """The Note this tag is inside, if any (a producer that put two notes in one Footnote tag)."""
        up = by_obj.get(e.parent.objgen) if e.parent is not None else None
        while up is not None and up.role != "Note":
            up = by_obj.get(up.parent.objgen) if up.parent is not None else None
        return up

    notes: dict[str, object] = {}  # an endnote's mark can be pages away from its note
    for page in analysis.pages:
        for b in page.blocks:
            if b.role != "Note":
                continue
            e = best(page.number, b.bbox, lambda e: e.role in ("Note", "P"))
            if e is None:
                continue
            if e.role == "P" and in_note(e) is not None:
                e = in_note(e)
            elif e.role == "P":
                if not _plain(e):
                    continue
                e.obj.S = Name.Note
                e.role = e.raw_role = "Note"
                existing.mark(e, "repaired", "footnote held as a paragraph: tagged as a Note")
                out["notes"] += 1
            notes[b.id] = e
    held = set(id(e) for e in notes.values())

    def point(e, targets: list, said: str | None) -> None:
        if any("/Ref" in d.obj for d in existing.descendants(e)):
            return  # the producer already says what it points at
        e.obj.Ref = pikepdf.Array([t.obj for t in targets])
        out["refs"] += 1
        note = "names its note in /Ref"
        if said and _plain(e):
            e.obj.ActualText = pikepdf.String(said)
            out["said"] += 1
            note += ", and says the mark as a note"
        existing.mark(e, "repaired", note)

    for page in analysis.pages if notes else []:
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

    candidates: dict[int, list] = {}
    for e in existing.elements:
        if e.decision in ("replaced", "removed", "artifacted") or not _plain(e):
            continue
        boxes = []
        for d in existing.descendants(e):
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
                union = bbox_area(bbox) + bbox_area(eb) - inter
                score = inter / union if union > 0 else 0.0
                if score > 0 and score >= best_score:  # of two that fit alike, the one further inside
                    best, best_score = e, score
            if best is not None and best_score >= 0.6:
                best.obj.ActualText = pikepdf.String(said)
                existing.mark(best, "repaired", "reading of specialist notation written as ActualText")
                used.add(id(best))
                placed += 1
            elif any(bbox_intersection(bbox, hb) >= 0.3 * (bbox_area(bbox) or 1.0) for hb in held.get(pi, [])):
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
