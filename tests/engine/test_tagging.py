from pathlib import Path

import pikepdf
import pymupdf as fitz
import pytest

from app.pipeline.orchestrator import prepare_analysis
from app.tagging.tagger import tag_document
from app.validation.audit import audit_pdf


def _pixels_equal(a: Path, b: Path) -> bool:
    da, db = fitz.open(a), fitz.open(b)
    return da.page_count == db.page_count and all(
        da[i].get_pixmap(dpi=72).samples == db[i].get_pixmap(dpi=72).samples for i in range(da.page_count)
    )


def _parent_tree_consistent(path: Path) -> None:
    """Every MCID in page content must map, via ParentTree, to an element whose K references it."""
    with pikepdf.open(path) as pdf:
        nums = list(pdf.Root.StructTreeRoot.ParentTree.Nums)
        tree = {int(nums[i]): nums[i + 1] for i in range(0, len(nums), 2)}
        for page in pdf.pages:
            mcids = []
            for ins in pikepdf.parse_content_stream(page.obj):
                if isinstance(ins, pikepdf.ContentStreamInstruction) and str(ins.operator) == "BDC":
                    props = ins.operands[1]
                    if isinstance(props, pikepdf.Dictionary) and "/MCID" in props:
                        mcids.append(int(props.MCID))
            if not mcids:
                continue
            arr = tree[int(page.obj.StructParents)]
            for mcid in mcids:
                elem = arr[mcid]
                k = elem.K
                refs = [int(x) for x in (k if isinstance(k, pikepdf.Array) else [k]) if isinstance(x, int)]
                assert mcid in refs, f"MCID {mcid} not referenced by its ParentTree element"


@pytest.mark.parametrize("name", ["simple_report.pdf", "two_column.pdf", "table_doc.pdf", "partially_tagged.pdf"])
def test_tagging_is_pixel_identical_and_complete(samples, tmp_path, name):
    src = samples / name
    analysis = prepare_analysis(src)
    for p in analysis.pages:
        for i in p.images:
            i.alt = "test alt"
    out = tmp_path / "out.pdf"
    tag_document(src, out, analysis, title="Test", language="en-US")

    assert _pixels_equal(src, out)
    _parent_tree_consistent(out)
    audit = audit_pdf(out)
    assert audit["tagged"] and audit["marked"]
    assert audit["content_tagged_ratio"] == 1.0
    assert audit["figures_missing_alt"] == 0
    assert audit["heading_level_skips"] == 0
    assert audit["language"] == "en-US"
    assert audit["pdfua_identifier"] == "1"


def test_table_structure(samples, tmp_path):
    analysis = prepare_analysis(samples / "table_doc.pdf")
    assert len(analysis.pages[0].tables) == 1
    out = tmp_path / "t.pdf"
    tag_document(samples / "table_doc.pdf", out, analysis, title="T", language="en-US")
    a = audit_pdf(out)
    assert a["tables"] == 1 and a["tables_without_headers"] == 0
    assert a["roles"]["TH"] == 3 and a["roles"]["TD"] == 9


def test_links_get_struct_and_contents(samples, tmp_path):
    analysis = prepare_analysis(samples / "simple_report.pdf")
    out = tmp_path / "s.pdf"
    tag_document(samples / "simple_report.pdf", out, analysis, title="T", language="en-US")
    with pikepdf.open(out) as pdf:
        annots = [a for a in pdf.pages[0].obj.Annots if a.Subtype == pikepdf.Name.Link]
        assert annots and all("/StructParent" in a and "/Contents" in a for a in annots)
    assert audit_pdf(out)["annotations_untagged"] == 0


def test_running_headers_are_artifacts(samples):
    analysis = prepare_analysis(samples / "simple_report.pdf")
    artifacts = [b.text for p in analysis.pages for b in p.blocks if b.role == "Artifact"]
    assert any("Page 1 of 2" in t for t in artifacts)
    assert any("ACME Annual Report" in t for t in artifacts)


def test_scanned_page_ocr(samples, tmp_path):
    """OCR adds an invisible text layer; the scan becomes an artifact, except the chart printed on it, which is
    drawn on its own and tagged as a Figure. The page looks exactly the same. Needs Tesseract (present in Docker)."""
    import asyncio
    import shutil
    import uuid

    from app.pipeline.ocr import tesseract_available
    from app.pipeline.orchestrator import JobOptions, run_remediation
    from app.storage import JobStore

    if not tesseract_available():
        pytest.skip("tesseract not installed")
    store = JobStore(str(uuid.uuid4()), root=tmp_path)
    store.create()
    shutil.copy(samples / "scanned.pdf", store.input_pdf)
    res = asyncio.run(run_remediation(store, JobOptions(stem="scanned", provider="mock")))
    assert res["metrics"]["ocr_pages"] == 1
    after = res["reports"]["comparison"]["after"]
    assert after["headings"] >= 1 and after["figures"] == 1 and res["metrics"]["scan_figures"] == 1
    assert res["reports"]["comparison"]["visual_fidelity"]["identical"]


@pytest.mark.asyncio
async def test_ai_cannot_drop_obvious_header_row(samples):
    from app.ai.mock import MockProvider
    from app.pipeline.enrichment import enrich

    class NoHeaders(MockProvider):
        async def analyze_table(self, png, page, grid):
            return {"header_rows": 0, "header_cols": 0, "spans": [], "summary": "s", "is_layout_table": False}

    analysis = prepare_analysis(samples / "table_doc.pdf")
    t = analysis.pages[0].tables[0]
    assert t.header_evidence
    async with NoHeaders() as ai:
        stats = await enrich(ai, samples / "table_doc.pdf", analysis)
    assert t.header_rows == 1 and any("kept row 1" in f for f in stats.findings)


def test_flattened_output_keeps_original_roles(samples, tmp_path):
    """Bold body-size headings must stay headings after destructive flattening (regular-weight text layer)."""
    from app.pipeline.transfer import transfer_enrichment
    from app.tagging.destructive import flatten_pages

    src = samples / "table_doc.pdf"
    orig = prepare_analysis(src)
    flat = tmp_path / "flat.pdf"
    flatten_pages(str(src), str(flat), [0])
    new = prepare_analysis(flat)
    transfer_enrichment(orig, new, {0}, set())
    roles = {b.text[:17]: b.role for b in new.pages[0].blocks}
    assert roles.get("Quarterly Results") == "H1"
    assert all(e in [b.id for b in new.pages[0].blocks if b.role != "Artifact"] + [i.id for i in new.pages[0].images]
               + [t.id for t in new.pages[0].tables] for e in new.pages[0].reading_order)


def test_headings_group_following_content_into_sections(samples, tmp_path):
    import pikepdf

    analysis = prepare_analysis(samples / "simple_report.pdf")
    out = tmp_path / "s.pdf"
    tag_document(samples / "simple_report.pdf", out, analysis, title="T", language="en-US")
    with pikepdf.open(out) as pdf:
        doc = pdf.Root.StructTreeRoot.K[0]
        top = [str(k.S) for k in doc.K]
        assert top == ["/Sect"]                       # everything sits under the H1's section
        h1_sect = doc.K[0]
        assert str(h1_sect.K[0].S) == "/H1"
        subs = [k for k in h1_sect.K if str(k.S) == "/Sect"]
        assert len(subs) == 2 and all(str(s.K[0].S) == "/H2" for s in subs)
        # page 2's H2 section holds its own list and figure (grouped with its caption), i.e. content follows
        # its heading
        kids = list(subs[1].K)
        assert "/L" in {str(k.S) for k in kids}
        assert any(str(k.S) == "/Sect" and [str(x.S) for x in k.K] == ["/Figure", "/Caption"] for k in kids)
    _parent_tree_consistent(out)
