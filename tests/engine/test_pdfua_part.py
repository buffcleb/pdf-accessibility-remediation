"""#409: a PDF 2.0 document can only be PDF/UA-2, so it's labelled and judged as PDF/UA-2; PDF 1.x stays PDF/UA-1."""
import copy

import pikepdf
import pymupdf as fitz

from app.pipeline.analyzer import analyze_document
from app.pipeline.orchestrator import _rules
from app.pipeline.outputs import snapshot
from app.reports.markdown import render_job_markdown
from app.reports.pdf import build_html
from app.tagging.tagger import PDF2_NS, tag_document
from app.validation import verapdf
from app.validation.verapdf import VeraResult, pdfua_part
from tests.engine.test_report_pdf import BRAND, META, RESULT


def _pdf(path, version):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Annual report", fontsize=20)
    page.insert_text((72, 140), "Sales rose in every region this year.", fontsize=11)
    doc.save(path.with_suffix(".tmp.pdf"))
    with pikepdf.open(path.with_suffix(".tmp.pdf")) as pdf:
        pdf.save(path, min_version=version)
    return path


def test_the_part_follows_the_files_version(tmp_path):
    assert pdfua_part(_pdf(tmp_path / "a.pdf", "2.0")) == 2
    assert pdfua_part(_pdf(tmp_path / "b.pdf", "1.7")) == 1
    bad = tmp_path / "c.pdf"
    bad.write_bytes(b"%PDF-2.0\nnot really a pdf")
    assert pdfua_part(bad) == 2  # unreadable: the header, as veraPDF reads it


def test_pdf_ua_2_gives_the_verdict_for_a_pdf_2_file_and_ua_1_still_runs(monkeypatch):
    assert verapdf.flavours(1) == ["ua1", "ua2"]
    assert verapdf.flavours(2) == ["ua2", "ua1"]
    # With the informational check turned off: PDF 1.x checks UA-1 only; PDF 2.0 still both, as its repairs need UA-1
    monkeypatch.setattr(verapdf, "_options", type(verapdf._options)("t", default=verapdf.ValidatorOptions(ua2=False)))
    assert verapdf.flavours(1) == ["ua1"] and verapdf.flavours(2) == ["ua2", "ua1"]


def test_the_verdict_names_its_part_and_repairs_go_by_ua1():
    ua2 = VeraResult(flavour="ua2", available=True, compliant=True)
    ua1 = VeraResult(flavour="ua1", available=True, compliant=False)
    snap = snapshot({}, {"ua2": ua2, "ua1": ua1})
    assert (snap["pdfua_part"], snap["compliant"]) == (2, True)
    assert snapshot({}, {"ua1": ua1, "ua2": ua2})["pdfua_part"] == 1
    assert _rules({"ua2": ua2, "ua1": ua1}) is ua1 and _rules({"ua2": ua2}) is ua2


def test_a_pdf_2_file_is_labelled_pdf_ua_2_with_its_document_in_the_pdf_2_namespace(tmp_path):
    for version, part in (("2.0", "2"), ("1.7", "1")):
        src = _pdf(tmp_path / f"in{version}.pdf", version)
        out = tmp_path / f"out{version}.pdf"
        result = tag_document(src, out, analyze_document(src), title="Annual report", language="en-US")
        with pikepdf.open(out) as pdf, pdf.open_metadata() as meta:
            assert str(pdf.pdf_version) == version
            assert meta.get("pdfuaid:part") == part
            assert meta.get("pdfuaid:rev") == ("2024" if part == "2" else None)
            (doc,) = [pdf.Root.StructTreeRoot.K] if isinstance(pdf.Root.StructTreeRoot.K, pikepdf.Dictionary) \
                else list(pdf.Root.StructTreeRoot.K)
            ns = str((doc.get("/NS") or {}).get("/NS", ""))
            assert ns == (PDF2_NS if part == "2" else "")
        assert ("Document tag put in the PDF 2.0 namespace" in result.metadata_changes) == (part == "2")


def test_the_reports_name_the_part_the_verdict_is_by():
    r = copy.deepcopy(RESULT)
    comp = r["reports"]["comparison"]
    for snap in (comp["before"], comp["after"]):
        snap["pdfua_part"] = 2
        snap["verapdf"] = {"ua2": snap["verapdf"]["ua1"], "ua1": {"failed_rules": 9, "failures": []}}
    h = build_html(META, r, BRAND)
    assert "the PDF/UA-2 accessibility check" in h and "Accessibility check (PDF/UA-2)" in h and "PDF/UA-1" not in h
    assert "Tables &lt;have&gt; header cells" in h  # the failures listed are PDF/UA-2's
    md = render_job_markdown("j", {"filename": "x.pdf"}, "completed", r)
    assert "PDF/UA-2" in md
