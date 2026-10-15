"""A figure tag the document arrived with and no description for: the AI describes the region after the first
tagging pass. The description used to be applied by tagging the whole document a second time; it is now written
onto the tag itself, with the report saying the same as before. Only a figure judged decorative still needs the
second pass (its content has to be re-marked)."""
import asyncio

import pikepdf
from pikepdf import Array, Dictionary, Name

from app.pipeline import orchestrator
from app.pipeline.orchestrator import JobOptions, run_remediation
from app.storage import JobStore
from app.verification.tagmap import build_tagmap


def _kept_figure_without_alt(path):
    """A kept tree: a paragraph, and a Figure over a small drawing (two shapes: too few for the layout to call it a
    picture, big enough not to be a hairline), with no /Alt."""
    pdf = pikepdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica,
                                        Encoding=Name.WinAnsiEncoding))
    content = (b"/P <</MCID 0>> BDC BT /F1 12 Tf 72 720 Td (A paragraph above the drawing.) Tj ET EMC\n"
               b"/Figure <</MCID 1>> BDC 0.2 0.4 0.8 rg 100 500 120 70 re f 0 0 0 RG 100 490 m 220 490 l S EMC")
    page = pdf.make_indirect(Dictionary(Type=Name.Page, MediaBox=Array([0, 0, 612, 792]),
                                        Resources=Dictionary(Font=Dictionary(F1=font)), Contents=pdf.make_stream(content)))
    pdf.Root.Pages = pdf.make_indirect(Dictionary(Type=Name.Pages, Kids=Array([page]), Count=1))
    page.Parent = pdf.Root.Pages
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    doc = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Document, P=root))
    para = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.P, P=doc, Pg=page, K=0))
    fig = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Figure, P=doc, Pg=page, K=1))
    doc.K = Array([para, fig])
    root.K = Array([doc])
    root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array([0, Array([para, fig])])))
    page.StructParents = 0
    pdf.Root.StructTreeRoot = root
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    pdf.save(path)


def test_a_description_is_written_onto_the_tag_without_tagging_again(tmp_path, monkeypatch):
    passes = []
    original = orchestrator.tag_document

    def counting(src, dst, *a, **k):
        if dst.name == "visual.pdf":  # the modified file (substitute fonts, flattening) is tagged on its own
            passes.append(k.get("region_alts"))
        return original(src, dst, *a, **k)
    monkeypatch.setattr(orchestrator, "tag_document", counting)
    store = JobStore("00000000-0000-4000-8000-000000000003", root=tmp_path)
    store.create()
    _kept_figure_without_alt(store.input_pdf)
    result = asyncio.run(run_remediation(store, JobOptions(stem="doc", provider="mock")))
    assert passes == [None], f"tag_document ran {len(passes)} times for the visual file"
    # The visually exact file: on its own, or beside a modified one where veraPDF is installed and finds the
    # unembedded font (CI's image does; a development machine may not)
    files = result["files"]
    visual = store.output_dir / (files["remediated_visual_fallback"] or files["remediated_primary"])
    alts = [str(e.get("alt")) for e in build_tagmap(visual)["elements"] if e["role"] == "Figure"]
    assert alts == ["Test illustration on page 1"]
    visual = result["reports"]["tagging"]["visual"]
    assert visual["figures_needing_alt"] == [] and "figure_handles" not in visual
    et = visual["existing_tags"]
    assert et["unresolved"] == 0 and et["repaired"] == 1 and et["retained"] == 2  # the Document and the paragraph
    assert any(s["decision"] == "repaired" and "missing Alt added" in s["reasons"] for s in et["samples"])
