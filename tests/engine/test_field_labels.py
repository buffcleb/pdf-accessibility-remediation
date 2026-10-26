"""#341: form fields are named from their printed labels (the AI names what the page doesn't), a radio button by its
question and option, internal names are replaced and readable tooltips kept, and a new tree's fields sit beside
their labels."""
import asyncio

import pikepdf
import pymupdf as fitz

from app.pipeline import field_labels
from tests.engine.pdfs import remediate


def _form(path):
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    text = [(72, 80, "Application for a library card"),
            (72, 130, "Last name:"),
            (72, 175, "Email address"),
            (72, 260, "Notify me by email"),
            (72, 320, "Preferred branch"),
            (100, 345, "Main"), (200, 345, "North"), (300, 345, "South"),
            (72, 420, "Thank you for applying.")]
    for x, y, t in text:
        page.insert_text((x, y), t, fontsize=11)

    def add(kind, rect, name, tooltip="", on=None):
        w = fitz.Widget()
        w.field_type, w.rect, w.field_name, w.field_label = kind, fitz.Rect(rect), name, tooltip
        if on is not None:
            w.button_caption, w.field_value = None, False
            w.on_state_name = on
        page.add_widget(w)
        return w

    add(fitz.PDF_WIDGET_TYPE_TEXT, (150, 118, 360, 134), "Text1")              # label left of it
    add(fitz.PDF_WIDGET_TYPE_TEXT, (72, 180, 300, 196), "fill_2")              # label above it
    add(fitz.PDF_WIDGET_TYPE_TEXT, (72, 210, 300, 226), "Text3", "Phone number")  # readable: kept
    add(fitz.PDF_WIDGET_TYPE_CHECKBOX, (215, 250, 227, 262), "Check Box4")    # its label is left of it
    for x, opt in ((86, "Main"), (186, "North"), (286, "South")):
        add(fitz.PDF_WIDGET_TYPE_RADIOBUTTON, (x, 336, x + 11, 347), "Group5", on=opt)
    add(fitz.PDF_WIDGET_TYPE_TEXT, (400, 500, 560, 516), "Text6")              # nothing printed near it
    doc.save(path)
    return path


def test_fields_are_named_from_the_page(tmp_path):
    src = _form(tmp_path / "form.pdf")
    labels, missing = field_labels.find(src)
    assert labels.fields["Text1"] == "Last name"
    assert labels.fields["fill_2"] == "Email address"
    assert "Text3" not in labels.fields  # "Phone number" already reads like words
    assert labels.fields["Check Box4"] == "Notify me by email"
    assert labels.fields["Group5"] == "Preferred branch"
    assert sorted(labels.widgets.values()) == ["Preferred branch: Main", "Preferred branch: North",
                                               "Preferred branch: South"]
    assert [f.name for f in missing] == ["Text6"]


def test_the_ai_names_what_the_page_doesnt(tmp_path):
    from app.ai.mock import MockProvider
    from app.pipeline.enrichment import EnrichmentStats

    src = _form(tmp_path / "form.pdf")
    labels, missing = field_labels.find(src)
    stats = EnrichmentStats()

    async def run():
        async with MockProvider() as ai:
            await field_labels.ask_ai(ai, src, missing, labels, limit=10, stats=stats)
    asyncio.run(run())
    assert labels.fields["Text6"] == "Mock field label" and labels.from_ai == 1 and not labels.unnamed
    # Past the limit: left for a person, and the limit is recorded
    labels2, missing2 = field_labels.find(src)
    stats2 = EnrichmentStats()

    async def run0():
        async with MockProvider() as ai:
            await field_labels.ask_ai(ai, src, missing2, labels2, limit=0, stats=stats2)
    asyncio.run(run0())
    assert labels2.unnamed == ["Text6"] and stats2.limits_hit[0]["setting"] == "AI_FIELD_LABELS_MAX"


def test_a_remediated_form_carries_the_names_with_fields_beside_their_labels(tmp_path):
    from app.verification.tagmap import build_tagmap

    res, store = remediate(tmp_path, _form(tmp_path / "form.pdf"), stem="f")
    out = store.output_dir / res["files"]["remediated_primary"]
    with pikepdf.open(out) as pdf:
        fields = list(pdf.Root.AcroForm.Fields)
        tu = {str(f.T): str(f.get("/TU", "")) for f in fields if str(f.T) != "Group5"}
        # PyMuPDF writes the radio group as three same-named fields: each button its own "question: option"
        buttons = sorted(str(f.get("/TU", "")) for f in fields if str(f.T) == "Group5")
    assert tu["Text1"] == "Last name" and tu["fill_2"] == "Email address" and tu["Text3"] == "Phone number"
    assert tu["Check Box4"] == "Notify me by email"
    assert tu["Text6"] == "Mock field label"
    assert buttons == ["Preferred branch: Main", "Preferred branch: North", "Preferred branch: South"]
    assert any("Form fields: 4 named from their printed labels, 1 by the AI" in w for w in res["warnings"])
    # Read in order: each field right after its label, not all of them after the page's text
    tm = build_tagmap(out)
    order = [e.get("text") or e.get("name") for e in sorted((e for e in tm["elements"] if e["own"]),
                                                             key=lambda e: e["order"])]
    assert order.index("Last name") < order.index("Thank you for applying.")
    assert order.index("Email address", order.index("Email address") + 1) < order.index("Thank you for applying.")


def test_the_review_flags_fields_left_with_an_internal_name(tmp_path):
    from app.pipeline.orchestrator import prepare_analysis
    from app.tagging.tagger import tag_document
    from tests.engine.test_verification import _semantic

    src = _form(tmp_path / "form.pdf")
    bare = tmp_path / "bare.pdf"
    tag_document(src, bare, prepare_analysis(src), title="Form", language="en-US")  # no labels found
    flagged = [f for f in _semantic(bare) if f.issue == "form field has no name a person would understand"]
    assert len(flagged) >= 5 and any('"Text1"' in f.detail for f in flagged)
    named = tmp_path / "named.pdf"
    labels, _ = field_labels.find(src)
    tag_document(src, named, prepare_analysis(src), title="Form", language="en-US", field_labels=labels)
    left = [f for f in _semantic(named) if f.issue == "form field has no name a person would understand"]
    assert [f.detail for f in left] == ['announced as "Text6"']  # only the one nothing on the page names


def test_the_tag_manager_sets_a_field_label(tmp_path):
    import pytest

    from app.pipeline.orchestrator import prepare_analysis
    from app.tagging.editor import EditError, apply_edits
    from app.tagging.tagger import tag_document
    from app.verification.tagmap import build_tagmap

    src = _form(tmp_path / "form.pdf")
    tagged = tmp_path / "tagged.pdf"
    tag_document(src, tagged, prepare_analysis(src), title="Form", language="en-US")
    tm = build_tagmap(tagged)
    field = next(e for e in tm["elements"] if e["role"] == "Form" and e.get("name") == "Text6")
    para = next(e for e in tm["elements"] if e["role"] == "P")
    out = tmp_path / "edited.pdf"
    apply_edits(tagged, out, [{"op": "set_field_label", "element": field["i"], "expect_role": "Form",
                               "label": "  Library card number  "}])
    with pikepdf.open(out) as pdf:
        assert {str(f.T): str(f.get("/TU", "")) for f in pdf.Root.AcroForm.Fields}["Text6"] == "Library card number"
    assert next(e for e in build_tagmap(out)["elements"] if e["i"] == field["i"])["name"] == "Library card number"
    with pytest.raises(EditError):  # only a field's tag has one
        apply_edits(tagged, tmp_path / "x.pdf", [{"op": "set_field_label", "element": para["i"], "label": "x"}])
    with pytest.raises(EditError):  # and it isn't empty
        apply_edits(tagged, tmp_path / "y.pdf", [{"op": "set_field_label", "element": field["i"], "label": " "}])
