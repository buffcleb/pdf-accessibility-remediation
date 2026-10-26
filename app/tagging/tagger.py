"""Non-destructive tagging of a whole document."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pikepdf
from pikepdf import Dictionary, Name

from app.pipeline.field_labels import Labels
from app.pipeline.field_labels import apply as apply_field_labels
from app.pipeline.models import BBox, DocumentAnalysis
from app.tagging.content_marker import ContentMarker, MarkResult, StreamMarks, count_form_uses, existing_mcids
from app.tagging.blank_fonts import fill_blank_fonts
from app.tagging.font_unicode import map_picture_glyphs
from app.tagging.tree import walk
from app.tagging import structure_rules
from app.tagging.existing_tags import ExistingTree
from app.tagging.kept_repair import repair_kept
from app.tagging.conformance import conformance
from app.tagging.metadata import apply_document_metadata
from app.tagging.self_links import drop_self_links
from app.tagging.placement import place_added
from app.tagging.reorder import reorder_retained
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
    empty_spans_removed: int = 0  # kept tags: Spans around nothing visible, folded into their parent
    # Tags put at the right level and figures' bounding boxes (structure_rules, #437/#438): what was done, by kind
    structure_rules: dict = field(default_factory=dict)
    groupings_artifacted: int = 0  # kept Art/Sect/Div/Part tags holding only lines or shapes, made decoration (#438)
    form_pages_retagged: int = 0  # kept figures holding a whole page of text drawn as one form, tagged afresh (#435)
    picture_glyphs_mapped: dict = field(default_factory=dict)  # font -> characters given private-use values
    picture_glyphs_in_text: list = field(default_factory=list)  # pages (1-based) where one stands in a sentence
    added_placed: int = 0  # kept tags: added tags put beside the kept tag they follow
    added_headings_demoted: int = 0  # ...and added "headings" made paragraphs (the document has its own outline)
    orphans_retagged: int = 0
    orphans_artifacted: int = 0  # untagged decorative pictures in the old tree, now artifacts
    blank_runs_artifacted: int = 0  # kept runs of only spaces in tags with no visible text (blank lines), #490
    links_given_words: int = 0  # kept Link tags that held only their annotation, given the words in its box (#489)
    artifacts_released: int = 0  # body text the old tagging hid as artifacts, now tagged
    containers_reordered: int = 0  # kept tags put in reading order
    hidden_text_artifacted: int = 0  # kept tags whose text is covered by shapes drawn over it
    off_page_artifacted: int = 0  # kept tags whose content is all outside the page's visible area (#438)
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


def _choose_route(pdf: pikepdf.Pdf, existing: ExistingTree, report, ignore_existing: bool) -> bool:
    """Whether the document's own tags are kept (True) or the tree is built afresh: when they can't be used, or the
    person asked for them to be ignored, they're stripped, with any StructParents keys left behind."""
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
    return retain


@dataclass
class _Glyphs:
    """Character codes of each font (by object), as the marker found them across the pages."""
    pictures: dict[tuple[int, int], set[int]] = field(default_factory=dict)  # drawn in figures and decoration
    body: dict[tuple[int, int], set[int]] = field(default_factory=dict)  # drawn in tagged text
    body_pages: dict[tuple[int, int], set[int]] = field(default_factory=dict)  # the pages of those
    counts: dict[tuple[int, int], list[int]] = field(default_factory=dict)  # [in pictures, in text]


def _add_up(results: list[MarkResult], marks: dict, result: TagResult) -> _Glyphs:
    """Each page's marker result into marks (the page's marked streams) and the document's totals."""
    glyphs = _Glyphs()
    for page_no, mr in enumerate(results):
        marks[page_no] = mr.streams
        result.tagged_units += mr.tagged_units
        result.artifacts += mr.artifacts
        result.existing_units += mr.existing_units
        result.stripped_marks += mr.stripped_marks
        result.forms_wrapped += mr.forms_wrapped
        result.artifacts_released += mr.released_artifacts
        for font, codes in mr.picture_glyphs.items():
            glyphs.pictures.setdefault(font, set()).update(codes)
        for font, codes in mr.body_glyphs.items():
            glyphs.body.setdefault(font, set()).update(codes)
            glyphs.body_pages.setdefault(font, set()).add(page_no)
        for font, (in_pictures, in_text) in mr.glyph_counts.items():
            total = glyphs.counts.setdefault(font, [0, 0])
            total[0] += in_pictures
            total[1] += in_text
    return glyphs


def _copy_builder_stats(s, result: TagResult) -> None:
    """What the tree builder counted, into the result."""
    result.elements = dict(s.elements)
    result.figures_with_alt, result.figures_without_alt = s.figures_with_alt, s.figures_without_alt
    result.tables, result.links, result.annotations = s.tables, s.links, s.annotations
    result.tables_linked = s.tables_linked
    result.footnotes, result.notes_given_ids = s.footnotes, s.notes_given_ids
    result.footnote_references = s.footnote_references
    result.annotation_text_added = s.contents_added
    result.unmatched_paragraphs = s.unmatched_paragraphs
    result.passages_lang = s.passages_lang
    result.headings_renumbered = s.headings_renumbered


FORM_PAGE_MIN_TEXT = 20  # text-showing operators: below this, a form is a picture with labels, not a page of text


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
        existing = ExistingTree(pdf, _stream_mcids(pdf), {pi: p.tables for pi, p in enumerate(analysis.pages)})
        report = existing.inspect()
        retain = _choose_route(pdf, existing, report, ignore_existing)
        kept_boxes: dict = {}  # kept figures' places (#437), when the document's tags are kept

        recursable = {og for og, n in count_form_uses(pdf).items() if n == 1}
        resolvers: dict[int, PageResolver] = {}
        marks: dict[int, list[StreamMarks]] = {}
        result = TagResult(existing_tags={})
        # The streams as they are, for the pages that have to be marked a second time (below)
        originals = _original_streams(pdf, recursable) if retain else {}

        def mark(page_no: int, release: dict, release_tags: dict | None = None) -> MarkResult:
            pa = analysis.pages[page_no]
            resolvers[page_no] = PageResolver(pa, layout_tables)
            marker = ContentMarker(pdf, pdf.pages[page_no], pa.pdf_to_page, resolvers[page_no],
                                   strip_existing=not retain, recursable_forms=recursable, release_artifacts=release,
                                   release_mcids=release_tags)
            return marker.run()

        results = [mark(page_no, {}) for page_no in range(len(pdf.pages))]
        if retain:
            # Body text a producer marked as an artifact (PowerPoint does this to text boxes) would be hidden from
            # screen readers. The pass just made says which artifact sections hold body text; the pages that have any
            # are put back as they were and marked again with those sections unwrapped, so their text is tagged.
            # Most documents have none, and are marked once.
            release = _body_artifacts(results)
            # A whole page of text placed as one form and tagged as one figure (#435): read as a picture, its text
            # is lost to a screen reader. Its tagged sequence is unwrapped and the form tagged from the inside, as
            # the layout reads it; the figure loses that content (and goes, when that was all it had)
            release_tags, form_pages = _form_pages(pdf, existing, results, recursable)
            for page_no, mr in enumerate(results):
                owners = {sm.owner.objgen for sm in mr.streams}
                if owners & (release.keys() | release_tags.keys()):
                    _restore_streams(pdf, pdf.pages[page_no], owners, originals)
                    results[page_no] = mark(page_no, release, release_tags)
            for e in form_pages:
                gone = [m for m in e.mcrefs if m.mcid in release_tags.get(m.owner, set())]
                existing.remove_refs(e, gone)
                e.mcrefs = [m for m in e.mcrefs if m not in gone]
                if not e.mcrefs and not e.children and not e.objrefs:
                    existing.mark(e, "removed", "a page of text drawn as one figure: tagged afresh from the layout")
            result.form_pages_retagged = len(form_pages)
            if form_pages:
                report.findings.append(
                    f"{len(form_pages)} figure tag(s) held a whole page of text drawn as one picture, which a screen "
                    "reader can't read: that text was tagged afresh from the page's layout")
        glyphs = _add_up(results, marks, result)

        if retain:
            kept_boxes = repair_kept(pdf, src, analysis, existing, report, marks, resolvers, result, region_alts)

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
                        builder.normalize_headings(top)
        _copy_builder_stats(builder.stats, result)
        result.fields_named = fields_named

        # Characters in figures and decoration that have no Unicode meaning (note heads, drawn line pieces)
        result.picture_glyphs_mapped, stray = map_picture_glyphs(pdf, glyphs.pictures, glyphs.body, glyphs.counts)
        # Pages where such a symbol stands in a sentence: nothing says what it is, so a person should
        result.picture_glyphs_in_text = sorted({p + 1 for font in stray for p in glyphs.body_pages.get(font, ())})
        # A PDF 2.0 file can only be PDF/UA-2 (#409): it's labelled as what it can be, and its Document is in the PDF
        # 2.0 namespace, as PDF/UA-2 asks (8.2.5.2)
        conf = conformance(str(pdf.pdf_version), pdfua_part)
        moved = conf.namespaces and _document_in_pdf2_namespace(pdf)
        result.metadata_changes = apply_document_metadata(pdf, title=title, language=language, pdfua_part=conf.part)
        self_links = drop_self_links(pdf)  # links that point to themselves (#484)
        if self_links:
            result.metadata_changes.append(f"{self_links} link(s) that only pointed to themselves removed (the "
                                           "words stay)")
        if moved:
            result.metadata_changes.append("Document tag put in the PDF 2.0 namespace")
        blank = fill_blank_fonts(pdf)
        if blank:
            result.metadata_changes.append(
                f"{len(blank)} font(s) of invisible text ({', '.join(sorted(set(blank))[:3])}) could only draw the "
                "missing glyph: each was given an empty glyph to draw (nothing on the page changes)")
        result.existing_tags = report.to_dict()
        # Tags at the right level, figures and formulas with their bounding box (#437, #438); before the figure
        # handles are taken, as a P put around inline tags changes the tree's walk
        result.structure_rules = structure_rules.apply(pdf, {**kept_boxes, **builder.boxes},
                                                       [pa.pdf_to_page for pa in analysis.pages])
        if result.figure_handles:
            # Object numbers don't survive the save; the element's place in a walk of the final tree does
            place = {e.objgen: n for n, e in enumerate(walk(root)) if e.is_indirect}
            result.figure_handles = [(pi, bbox, place.get(og), si, kept) for pi, bbox, og, si, kept in result.figure_handles]
        pdf.save(dst)
    return result


def _form_pages(pdf: pikepdf.Pdf, existing: ExistingTree, results: list, recursable: set) -> tuple[dict, list]:
    """The kept Figures and Formulas whose content is a whole page of text placed as a form (#435): each of their
    tagged sequences draws Form XObjects, each used once, with no tagged content of its own and many lines of text.
    Returns the sequences to unwrap (owner objgen -> MCIDs) and the tags."""
    import re

    forms_by_owner = {sm.owner.objgen: sm.existing_forms for mr in results for sm in mr.streams}
    verdict: dict = {}

    def text_page(og) -> bool:
        if og not in verdict:
            ok = False
            if og in recursable:
                try:
                    form = pdf.get_object(og)
                    data = form.read_bytes()
                    ok = ("/StructParents" not in form and "/StructParent" not in form and b"/MCID" not in data
                          and len(re.findall(rb"T[jJ]\b", data)) >= FORM_PAGE_MIN_TEXT)
                except (pikepdf.PdfError, AttributeError, TypeError, ValueError):
                    ok = False
            verdict[og] = ok
        return verdict[og]

    release: dict = {}
    chosen = []
    for e in existing.elements:
        if e.role not in ("Figure", "Formula") or not e.mcrefs or not all(m.valid for m in e.mcrefs):
            continue
        forms = [forms_by_owner.get(m.owner, {}).get(m.mcid, []) for m in e.mcrefs]
        if all(f and all(text_page(og) for og in f) for f in forms):
            chosen.append(e)
            for m in e.mcrefs:
                release.setdefault(m.owner, set()).add(m.mcid)
    return release, chosen


NO_DESCRIPTION = "missing Alt (no description available)"
DESCRIPTION_ADDED = "missing Alt added"


def write_figure_alts(path: Path, handles: list, alts: list[tuple[int, BBox, str]]) -> list:
    """Write descriptions found after tagging onto the figure tags the document arrived with, in place, and return
    the handles written. Each alt is matched to its handle by page and region. The same as tagging the document
    again with these descriptions, without the second pass: the tag gets its /Alt, as ExistingTree.repair would
    have given it."""
    wanted = {(pi, tuple(bbox)): alt for pi, bbox, alt in alts if alt}
    written = []
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        root = pdf.Root.get("/StructTreeRoot")
        elements = walk(root) if root is not None else []
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


