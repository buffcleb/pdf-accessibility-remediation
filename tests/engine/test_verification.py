"""Tag verification: seeded defects must be caught (Layer 1), semantic checks fire on real
problems (Layer 2), and the AI review layer routes and reports (Layer 3, mock judge)."""
import asyncio

import pikepdf
import pymupdf as fitz
import pytest
from pikepdf import Array, Dictionary, Name

from app.pipeline.orchestrator import prepare_analysis
from app.tagging.tagger import tag_document
from app.verification.integrity import check_integrity
from app.verification.semantic import check_semantics
from app.verification.verify import VerifyOptions, verify_output
from tests.engine.pdfs import integrity_errors


@pytest.fixture(scope="module")
def good(samples, tmp_path_factory):
    out = tmp_path_factory.mktemp("verify") / "good.pdf"
    analysis = prepare_analysis(samples / "simple_report.pdf")
    for p in analysis.pages:
        for i in p.images:
            i.alt = "Bar chart of remediation progress by quarter"
    tag_document(samples / "simple_report.pdf", out, analysis, title="T", language="en-US")
    return out


def _elements_with_mcid(pdf):
    root = pdf.Root.StructTreeRoot
    out, stack = [], [root.K]
    while stack:
        k = stack.pop()
        for it in (list(k) if isinstance(k, Array) else [k]):
            if isinstance(it, Dictionary) and "/S" in it:
                if isinstance(it.get("/K"), Array) and any(isinstance(x, int) for x in it.K):
                    out.append(it)
                if "/K" in it:
                    stack.append(it.K)
    return out


def test_clean_output_has_no_integrity_errors(good):
    assert integrity_errors(good) == []


def _mutate(good, tmp_path, fn, name="bad.pdf"):
    dst = tmp_path / name
    with pikepdf.open(good) as pdf:
        fn(pdf)
        pdf.save(dst)
    return dst


def _rewrite_ops(pdf, fn):
    page = pdf.pages[0]
    ops = fn(list(pikepdf.parse_content_stream(page.obj)))
    page.obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(ops))


def _bdc_indexes(ops):
    return [i for i, o in enumerate(ops) if isinstance(o, pikepdf.ContentStreamInstruction)
            and str(o.operator) == "BDC" and isinstance(o.operands[1], Dictionary) and "/MCID" in o.operands[1]]


def _set_mcid(ops, i, mcid):
    tag = ops[i].operands[0]
    ops[i] = pikepdf.ContentStreamInstruction([tag, Dictionary(MCID=mcid)], pikepdf.Operator("BDC"))


DEFECTS = {
    "parent tree entry removed": (
        lambda pdf: pdf.Root.StructTreeRoot.ParentTree.__setattr__("Nums", Array(list(pdf.Root.StructTreeRoot.ParentTree.Nums)[2:])),
        "ParentTree"),
    "duplicate MCID in content": (
        lambda pdf: _rewrite_ops(pdf, lambda ops: (_set_mcid(ops, _bdc_indexes(ops)[1], 0), ops)[1]),
        "more than once"),
    "content not connected": (
        lambda pdf: _elements_with_mcid(pdf)[0].__setattr__("K", Array()),
        "not connected"),
    "dangling reference": (
        lambda pdf: _elements_with_mcid(pdf)[0].K.append(999),
        "does not exist"),
    "content referenced twice": (
        lambda pdf: _elements_with_mcid(pdf)[1].K.append(int(_elements_with_mcid(pdf)[0].K[0])),
        "more than one"),
    "wrong parent pointer": (
        lambda pdf: _elements_with_mcid(pdf)[0].__setattr__("P", pdf.Root.StructTreeRoot),
        "/P does not point"),
    "unmapped custom role": (
        lambda pdf: _elements_with_mcid(pdf)[0].__setattr__("S", Name("/MadeUpRole")),
        "not standard"),
    "unbalanced EMC": (
        lambda pdf: _rewrite_ops(pdf, lambda ops: ops + [pikepdf.ContentStreamInstruction([], pikepdf.Operator("EMC"))]),
        "EMC without"),
    "nested MCID": (
        lambda pdf: _rewrite_ops(pdf, lambda ops: ops[:_bdc_indexes(ops)[0] + 1] + [
            pikepdf.ContentStreamInstruction([Name("/Span"), Dictionary(MCID=500)], pikepdf.Operator("BDC")),
            pikepdf.ContentStreamInstruction([], pikepdf.Operator("EMC"))] + ops[_bdc_indexes(ops)[0] + 1:]),
        "nested"),
    "annotation StructParent removed": (
        lambda pdf: pdf.pages[0].obj.Annots[0].__delitem__("/StructParent"),
        "/StructParent"),
}


@pytest.mark.parametrize("name", list(DEFECTS))
def test_seeded_defect_is_caught(good, tmp_path, name):
    mutate, expected = DEFECTS[name]
    bad = _mutate(good, tmp_path, mutate)
    errors = integrity_errors(bad)
    assert errors, f"{name}: not detected"
    assert any(expected.lower() in (e.issue + " " + (e.detail or "")).lower() for e in errors), \
        f"{name}: got {[e.issue for e in errors]}"


# ----------------------------------------------------------------------------- Layer 2
def _semantic(path):
    with pikepdf.open(path) as pdf:
        _, index = check_integrity(pdf)
        findings, _ = check_semantics(path, pdf, index)
    return findings


def test_semantic_flags_body_text_heading_and_filename_alt(good, tmp_path):
    def mutate(pdf):
        for el in _elements_with_mcid(pdf):
            if str(el.S) == "/P":
                el.S = Name("/H2")  # a body paragraph pretending to be a heading
                break
        for el in _elements_with_mcid(pdf):
            if str(el.S) == "/Figure":
                el.Alt = pikepdf.String("IMG_0042.jpg")
    findings = _semantic(_mutate(good, tmp_path, mutate))
    issues = {f.issue for f in findings}
    assert "heading looks like body text" in issues or "heading is unusually long" in issues
    assert "alt text is a file name or generic word" in issues


def test_a_figure_without_alt_text_is_listed_for_a_person(good, tmp_path):
    """#659: a document made without AI has its figures left without alt text; each is a review item, not only a
    veraPDF failure, so a person is asked to describe it."""
    assert not any(f.issue == "figure has no alt text" for f in _semantic(good))

    def mutate(pdf):
        for el in _elements_with_mcid(pdf):
            if str(el.S) == "/Figure":
                del el["/Alt"]
    found = [f for f in _semantic(_mutate(good, tmp_path, mutate)) if f.issue == "figure has no alt text"]
    assert found and all(f.severity == "high" and f.role == "Figure" and "Edit tag" in f.action for f in found)


def test_every_figure_without_alt_text_keeps_its_own_row():
    """Other findings fold into "(and N more like this)" past 25; a figure to describe is each one's own to-do."""
    from app.verification.model import Finding
    from app.verification.semantic import SemanticChecker
    many = [Finding("semantic", "high", "figure has no alt text", element=i) for i in range(40)]
    many += [Finding("semantic", "suggest", "alt text is very short", element=i) for i in range(40)]
    out = SemanticChecker._cap(many)
    assert sum(f.issue == "figure has no alt text" for f in out) == 40
    assert sum(f.issue == "alt text is very short" for f in out) == 25


def test_identical_images_sharing_alt_are_not_flagged(good):
    # simple_report draws the same picture on both pages with the same alt: that is fine.
    assert not any(f.issue == "same alt text on different images" for f in _semantic(good))


def test_language_mismatch_flagged(tmp_path):
    src = tmp_path / "lang.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(fitz.Rect(50, 50, 550, 150), "This syllabus describes the course and its weekly schedule "
                        "of readings, labs and assignments for the semester.", fontsize=11)
    page.insert_textbox(fitz.Rect(50, 170, 550, 270), "Ce programme est également disponible en français sur "
                        "demande auprès du secrétariat du département.", fontsize=11)
    doc.save(src)
    out = tmp_path / "lang_tagged.pdf"
    tag_document(src, out, prepare_analysis(src), title="T", language="en-US")
    # A sentence or more, the detector very sure: tagged French (#340), so the review has nothing to flag
    with pikepdf.open(out) as pdf:
        assert [str(o.Lang) for o in pdf.objects if isinstance(o, pikepdf.Dictionary) and "/Lang" in o
                and o.get("/Type") == "/StructElem"] == ["fr"]
    assert not [f for f in _semantic(out) if f.issue.startswith("passage in a different language")]
    # Shorter than that: left for a person, and flagged
    short = tmp_path / "short.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_textbox(fitz.Rect(50, 50, 550, 150), "This syllabus describes the course and its weekly schedule "
                        "of readings, labs and assignments for the semester.", fontsize=11)
    page.insert_textbox(fitz.Rect(50, 170, 550, 270), "Ce programme est disponible en français sur demande.",
                        fontsize=11)
    doc.save(short)
    out2 = tmp_path / "short_tagged.pdf"
    tag_document(short, out2, prepare_analysis(short), title="T", language="en-US")
    lang = [f for f in _semantic(out2) if f.issue.startswith("passage in a different language")]
    assert lang and lang[0].extra["detected_lang"] == "fr"


# ----------------------------------------------------------------------------- Layer 3 + entry point
def test_ai_review_flags_wrong_alt_with_mock_judge(good, tmp_path, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "verify_judge_provider", "mock")
    monkeypatch.setattr(get_settings(), "verify_judge_model", "mock-judge")
    bad = _mutate(good, tmp_path, lambda pdf: [el.__setattr__("Alt", pikepdf.String("MISMATCH description"))
                                               for el in _elements_with_mcid(pdf) if str(el.S) == "/Figure"])
    rep = asyncio.run(verify_output(bad, VerifyOptions(ai=True, full_review=True, seed="t")))
    assert rep.integrity_passed
    ai = [f for f in rep.findings if f.layer == "ai"]
    assert ai and ai[0].issue == "alt text does not match the image"
    assert "Mock corrected description" in (ai[0].action or "")
    assert rep.ai["results"]["inaccurate"] >= 1 and rep.ai["mode"] == "full"


def test_sample_mode_reviews_flagged_plus_sample(good, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "verify_judge_provider", "mock")
    rep = asyncio.run(verify_output(good, VerifyOptions(ai=True, full_review=False, seed="t")))
    assert rep.ai["mode"].startswith("flagged")
    assert rep.ai["figures_selected"] <= rep.ai["figures_total"]


def test_integrity_failure_raises_in_pipeline(good, tmp_path):
    from app.verification.model import IntegrityError

    bad = _mutate(good, tmp_path, DEFECTS["dangling reference"][0])
    rep = asyncio.run(verify_output(bad, VerifyOptions(ai=False)))
    assert not rep.integrity_passed
    with pytest.raises(IntegrityError):
        raise IntegrityError("primary", rep.errors)


def test_bad_ai_merge_cannot_orphan_table_content(samples, tmp_path):
    """Regression: a model claimed header cells spanned 2 rows; the cells beneath were dropped
    and their content left untagged. The verifier caught it; now it must not happen at all."""
    from app.ai.mock import MockProvider
    from app.pipeline.enrichment import enrich

    class OverMerge(MockProvider):
        async def analyze_table(self, png, page, grid):
            return {"header_rows": 1, "header_cols": 0, "summary": "s", "is_layout_table": False,
                    "spans": [{"row": 0, "col": 0, "row_span": 2, "col_span": 3}]}  # covers real cells

    src = samples / "table_doc.pdf"
    analysis = prepare_analysis(src)
    async def run():
        async with OverMerge() as ai:
            return await enrich(ai, src, analysis)
    stats = asyncio.run(run())
    assert any("ignored AI merge" in f for f in stats.findings)
    out = tmp_path / "t.pdf"
    tag_document(src, out, analysis, title="T", language="en-US")
    assert integrity_errors(out) == []


def test_normalisation_never_drops_cells_with_content():
    from app.pipeline.models import TableCell, TableRegion

    cells = [TableCell(0, 0, (0, 0, 1, 1), "Exams", row_span=2, col_span=3)]  # wrong span from a model
    cells += [TableCell(1, c, (c, 1, c + 1, 2), str(c)) for c in range(3)]
    t = TableRegion("t", 0, (0, 0, 3, 2), 2, 3, cells)
    kept = {c.text for c in t.normalized_cells() if c.text}
    assert kept == {"Exams", "0", "1", "2"}


def test_unreachable_judge_is_reported_not_silent(good, monkeypatch):
    from app.ai.base import AIError
    from app.ai.mock import MockProvider
    from app.config import get_settings

    async def down(self):
        raise AIError("cannot reach judge")

    monkeypatch.setattr(get_settings(), "verify_judge_provider", "mock")
    monkeypatch.setattr(MockProvider, "preflight", down)
    rep = asyncio.run(verify_output(good, VerifyOptions(ai=True, full_review=True, seed="t")))
    assert "unavailable" in rep.ai["error"] and rep.ai["reviewed"] == 0


def test_tounicode_gaps_are_filled_from_glyph_names(tmp_path):
    """TeX extension fonts ship a stock ToUnicode that misses codes a document uses."""
    from app.tagging.font_unicode import _cmap, glyph_to_unicode, parse_tounicode

    assert glyph_to_unicode("parenleftbigg") == "(" and glyph_to_unicode("summationdisplay") == "∑"
    assert glyph_to_unicode("braceleftBig") == "{" and glyph_to_unicode("A") == "A"
    assert glyph_to_unicode("totallyunknownglyph") is None
    cm = _cmap({0x12: "(", 0x58: "∑"})
    assert parse_tounicode(cm) == {0x12: "(", 0x58: "∑"}



def test_winansi_and_macroman_fonts_get_a_map_of_their_encoding(tmp_path):
    """A non-breaking space (WinAnsi 0xA0) in a TrueType font with no map: PAC can't map it. Non-symbolic fonts with
    WinAnsi or MacRoman get a map from the encoding; a symbolic font, one with a map, and other encodings don't."""
    import pikepdf
    from pikepdf import Dictionary, Name

    from app.tagging.font_unicode import add_missing_tounicode, parse_tounicode

    pdf = pikepdf.new()
    pdf.add_blank_page()

    def font(base, enc, flags, **kw):
        return pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.TrueType, BaseFont=Name(base), Encoding=enc,
                                            FontDescriptor=Dictionary(Type=Name.FontDescriptor, Flags=flags), **kw))

    fonts = Dictionary(F1=font("/Win", Name.WinAnsiEncoding, 32), F2=font("/Mac", Name.MacRomanEncoding, 32),
                       F3=font("/Sym", Name.WinAnsiEncoding, 4),
                       F4=font("/Mapped", Name.WinAnsiEncoding, 32, ToUnicode=pdf.make_stream(b"")),
                       F5=font("/Std", Name.StandardEncoding, 32),
                       F6=font("/Diff", pdf.make_indirect(Dictionary(Type=Name.Encoding,
                                                                     BaseEncoding=Name.WinAnsiEncoding)), 32))
    pdf.pages[0].Resources = Dictionary(Font=fonts)
    src, dst = tmp_path / "f.pdf", tmp_path / "o.pdf"
    pdf.save(src)
    fixed = add_missing_tounicode(src, dst)
    assert [f.split(" ")[0] for f in fixed] == ["Win", "Mac"]
    with pikepdf.open(dst) as out:
        f = out.pages[0].Resources.Font
        win, mac = parse_tounicode(f.F1.ToUnicode.read_bytes()), parse_tounicode(f.F2.ToUnicode.read_bytes())
        assert (win[0xA0], win[0xAD], win[0x93], win[0x41]) == ("\u00a0", "\u00ad", "\u201c", "A")
        assert 0x81 not in win and 0x1F not in win  # a code WinAnsi leaves out; a control code
        assert (mac[0xCA], mac[0xD2]) == ("\u00a0", "\u201c")
        assert "/ToUnicode" not in f.F3 and "/ToUnicode" not in f.F5 and "/ToUnicode" not in f.F6
        assert f.F4.ToUnicode.read_bytes() == b""

def test_same_figure_in_two_outputs_is_judged_once(good, tmp_path, monkeypatch):
    """The visually exact and modified files share most figures: one verdict serves both."""
    import shutil

    from app.ai.mock import MockProvider
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "verify_judge_provider", "mock")
    calls = []
    real = MockProvider.judge_alt

    async def counting(self, *a, **kw):
        calls.append(1)
        return await real(self, *a, **kw)

    monkeypatch.setattr(MockProvider, "judge_alt", counting)
    twin = tmp_path / "twin.pdf"
    shutil.copyfile(good, twin)
    cache: dict = {}
    first = asyncio.run(verify_output(good, VerifyOptions(ai=True, full_review=True, seed="t", review_cache=cache)))
    n = len(calls)
    second = asyncio.run(verify_output(twin, VerifyOptions(ai=True, full_review=True, seed="t", review_cache=cache)))
    assert n >= 1 and len(calls) == n  # nothing judged twice
    assert second.ai["results"] == first.ai["results"] and second.ai.get("reused") == n


def test_a_labeled_drawing_may_have_a_long_description(tmp_path):
    """A reaction scheme of several steps needs more than 300 characters to say what reacts and what forms; every
    such figure was reported as "alt text is very long". A picture's limit is unchanged."""
    from app.pipeline.models import ImageRegion, bbox_union

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "A diagram with labels on it follows, and then a photograph.", fontsize=11, fontname="helv")
    shape = page.new_shape()
    for k in range(6):
        shape.draw_line((150 + 20 * k, 300), (160 + 20 * k, 320))
    shape.finish(color=(0, 0, 0), width=1)
    shape.commit()
    page.insert_text((180, 296), "Step", fontsize=9, fontname="helv")
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 60, 60), False)
    pix.set_rect(pix.irect, (40, 90, 160))
    page.insert_image(fitz.Rect(150, 450, 270, 570), pixmap=pix)
    doc.save(tmp_path / "d.pdf")
    analysis = prepare_analysis(tmp_path / "d.pdf")
    pa = analysis.pages[0]
    label = next(b for b in pa.blocks if b.text == "Step")
    pa.blocks = [b for b in pa.blocks if b is not label]
    drawing = ImageRegion(id="p0d0", page=0, bbox=bbox_union([(148, 285, 262, 322), label.bbox]), xref=0, kind="vector",
                          holds_pieces=True, labels="Step")
    pa.images = [i for i in pa.images if i.xref] + [drawing]
    long_alt = "A sequence of six steps, each drawn as a slanted stroke, the third of them labeled. " * 5
    assert 300 < len(long_alt) <= 1000
    for img in pa.images:
        img.alt, img.decorative = long_alt, False
    pa.reading_order = [b.id for b in pa.blocks] + [i.id for i in pa.images]
    dst = tmp_path / "out.pdf"
    tag_document(tmp_path / "d.pdf", dst, analysis, title="Long", language="en-US")
    long = [f for f in _semantic(dst) if f.issue == "alt text is very long"]
    assert len(long) == 1, [f.issue for f in _semantic(dst)]   # the photograph's, not the labeled drawing's


def test_a_labeled_figure_on_a_flattened_page_may_have_a_long_description_too(tmp_path):
    """A flattened page's scheme is a picture with its labels as the text layer over it: with no strokes left, it
    was held to a photograph's 300 characters and reported on every flattened page."""
    from app.pipeline.models import ImageRegion

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "A picture with labels over it follows, and then a plain one.", fontsize=11, fontname="helv")
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 60, 60), False)
    for n, top in enumerate((200, 450)):
        pix.set_rect(pix.irect, (40 + 90 * n, 90, 160))
        page.insert_image(fitz.Rect(150, top, 330, top + 120), pixmap=pix)
    page.insert_text((170, 260), "OH", fontsize=9, fontname="helv", render_mode=3)   # invisible, as a text layer is
    doc.save(tmp_path / "f.pdf")
    analysis = prepare_analysis(tmp_path / "f.pdf")
    pa = analysis.pages[0]
    pa.blocks = [b for b in pa.blocks if b.text.strip() != "OH"]
    long_alt = "A ketone is reduced to the alcohol, which is then protected, in two steps shown left to right. " * 4
    assert 300 < len(long_alt) <= 1000
    pictures = sorted((i for i in pa.images if i.xref), key=lambda i: i.bbox[1])
    assert len(pictures) == 2
    labeled = ImageRegion(id="p0s0", page=0, bbox=pictures[0].bbox, xref=pictures[0].xref, holds_pieces=True, labels="OH")
    pa.images = [labeled, pictures[1]]
    for img in pa.images:
        img.alt, img.decorative = long_alt, False
    pa.reading_order = [b.id for b in pa.blocks] + [i.id for i in pa.images]
    dst = tmp_path / "out.pdf"
    tag_document(tmp_path / "f.pdf", dst, analysis, title="Long", language="en-US")
    found = _semantic(dst)
    assert len([f for f in found if f.issue == "alt text is very long"]) == 1, [f.issue for f in found]
