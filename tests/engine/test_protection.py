"""#339: a signed PDF is noticed (the portal holds it; the report says its signature won't survive), and a PDF that
restricts what can be done with it keeps the same restrictions on every output, with assistive technology allowed."""
import asyncio
import shutil
import uuid

import pikepdf

from app.pipeline import protection
from tests.engine.pdfs import remediate


def _restricted(src, dst, **refused):
    allow = pikepdf.Permissions(**{**{k: True for k in protection.PERMISSIONS}, **{k: False for k in refused}})
    with pikepdf.open(src) as pdf:
        pdf.save(dst, encryption=pikepdf.Encryption(owner="owner-secret", user="", allow=allow))
    return dst


def _signed(src, dst, certified=False):
    with pikepdf.open(src) as pdf:
        sig = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Sig, Filter=pikepdf.Name("/Adobe.PPKLite")))
        field = pdf.make_indirect(pikepdf.Dictionary(FT=pikepdf.Name.Sig, T=pikepdf.String("Signature1"), V=sig))
        pdf.Root.AcroForm = pikepdf.Dictionary(Fields=pikepdf.Array([field]), SigFlags=0)
        if certified:
            pdf.Root.Perms = pikepdf.Dictionary(DocMDP=sig)
        pdf.save(dst)
    return dst


def test_signed_and_restricted_pdfs_are_recognized(samples, tmp_path):
    plain = samples / "partially_tagged.pdf"
    assert protection.inspect(plain) == {"signed": False, "restricted": None, "xfa": None}
    assert protection.inspect(_signed(plain, tmp_path / "s.pdf"))["signed"]
    assert protection.inspect(_signed(plain, tmp_path / "c.pdf", certified=True))["signed"]
    info = protection.inspect(_restricted(plain, tmp_path / "r.pdf", print_highres=True, modify_other=True))
    assert info["signed"] is False and info["restricted"]["modify_other"] is False and info["restricted"]["extract"]
    # Encrypted with everything allowed restricts nothing
    assert protection.inspect(_restricted(plain, tmp_path / "open.pdf"))["restricted"] is None
    assert protection.inspect(tmp_path / "missing.pdf") == {}


def test_the_reports_say_what_was_found():
    signed = protection.notes({"signed": True, "restricted": None}, report_only=False)
    assert signed == ["This PDF is signed (or certified). The remediated copy doesn't carry a valid signature: sign it "
                      "again if one is needed."]
    r = {k: True for k in protection.PERMISSIONS} | {"modify_other": False, "accessibility": False}
    (kept,) = protection.notes({"signed": False, "restricted": r}, report_only=False)
    assert "restricts editing" in kept and "keeps the same restrictions" in kept and "assistive technology allowed" in kept
    (audit,) = protection.notes({"signed": False, "restricted": r}, report_only=True)
    assert "doesn't let assistive technology read it" in audit
    assert protection.notes({}, report_only=False) == []


def test_outputs_get_the_restrictions_back_with_assistive_technology_allowed(samples, tmp_path):
    src = _restricted(samples / "partially_tagged.pdf", tmp_path / "r.pdf", modify_other=True, accessibility=True,
                      print_highres=True)
    info = protection.inspect(src)
    out = tmp_path / "out"
    out.mkdir()
    with pikepdf.open(src) as pdf:  # what a run writes: no encryption
        pdf.save(out / "doc_remediated.pdf")
    (out / "doc_report.md").write_text("report")
    protection.finish(out, {"files": {"remediated_primary": "doc_remediated.pdf", "remediated_visual_fallback": None,
                                      "note": "doc_report.md"}}, info)
    with pikepdf.open(out / "doc_remediated.pdf") as pdf:  # still opens without a password
        assert pdf.is_encrypted
        assert pdf.allow.accessibility and not pdf.allow.modify_other and not pdf.allow.print_highres
        assert pdf.allow.extract and pdf.allow.print_lowres
    assert (out / "doc_report.md").read_text() == "report"
    # Nothing restricted: nothing changed
    plain = tmp_path / "plain.pdf"
    shutil.copy(samples / "partially_tagged.pdf", plain)
    protection.finish(tmp_path, {"files": {"remediated_primary": "plain.pdf"}}, {"signed": True, "restricted": None})
    with pikepdf.open(plain) as pdf:
        assert not pdf.is_encrypted


def test_a_restricted_document_runs_and_says_so_in_its_report(samples, tmp_path):
    from app.pipeline.orchestrator import JobOptions, run_remediation
    from app.storage import JobStore

    jid = str(uuid.uuid4())
    store = JobStore(jid, root=tmp_path / "jobs")
    store.create()
    _restricted(samples / "partially_tagged.pdf", store.input_pdf, modify_other=True)
    info = protection.inspect(store.input_pdf)
    result = asyncio.run(run_remediation(store, JobOptions(stem="r", job_id=jid, provider="mock")))
    assert any("keeps the same restrictions" in w for w in result["warnings"])
    assert "keeps the same restrictions" in next(store.dir.rglob("r_remediation_report.md")).read_text()
    protection.finish(store.output_dir, result, info)
    with pikepdf.open(store.output_dir / result["files"]["remediated_primary"]) as pdf:
        assert pdf.is_encrypted and pdf.allow.accessibility and not pdf.allow.modify_other


def _xfa(src, dst, dynamic=False):
    with pikepdf.open(src) as pdf:
        xfa = pdf.make_stream(b"<xdp:xdp xmlns:xdp='http://ns.adobe.com/xdp/'></xdp:xdp>")
        field = pikepdf.Dictionary(FT=pikepdf.Name.Tx, T=pikepdf.String("Name"))
        fields = [] if dynamic else [pdf.make_indirect(field)]
        pdf.Root.AcroForm = pikepdf.Dictionary(Fields=pikepdf.Array(fields), XFA=xfa)
        if dynamic:
            pdf.Root.NeedsRendering = True
        pdf.save(dst)
    return dst


def test_xfa_forms_are_told_apart_and_a_static_ones_xfa_is_removed(samples, tmp_path):
    """#360: dynamic XFA (no standard form) can't be made accessible and is refused at upload; static XFA keeps its
    standard form, and the XFA part goes."""
    plain = samples / "partially_tagged.pdf"
    static = _xfa(plain, tmp_path / "static.pdf")
    assert protection.inspect(static)["xfa"] == "static"
    assert protection.inspect(_xfa(plain, tmp_path / "dyn.pdf", dynamic=True))["xfa"] == "dynamic"
    (note,) = protection.notes({"xfa": "static"}, report_only=False)
    assert "XFA part was removed" in note
    res = _run(tmp_path, static)
    assert any("XFA part was removed" in w for w in res[0]["warnings"])
    with pikepdf.open(res[1]) as pdf:
        assert "/XFA" not in pdf.Root.AcroForm and "/NeedsRendering" not in pdf.Root


def _run(tmp_path, src):
    result, store = remediate(tmp_path, src, stem="x")
    return result, store.output_dir / result["files"]["remediated_primary"]
