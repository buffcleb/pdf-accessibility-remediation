"""PDFs built and checked by the tagging tests: a one-page file written by hand, the integrity check's errors, a
whole mock-provider run, and the same-render check. Imported explicitly (from tests.engine.pdfs import ...)."""
import asyncio
import shutil
import uuid

import pikepdf
import pymupdf as fitz
from pikepdf import Array, Dictionary, Name

from app.verification.integrity import check_integrity


def helvetica(pdf):
    """Helvetica, WinAnsi: the font the one-page files write their text in."""
    return pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica,
                                        Encoding=Name.WinAnsiEncoding))


def one_page(path, content, fonts=None):
    """A new file of one 612 x 792 page drawing `content` (bytes, or a list of lines) with Helvetica as /F1, and
    `fonts` beside it ({"M": make} for a /M made by make(pdf)). Saved to `path` unless it is None, for a caller
    adding a tree first; returns (pdf, page)."""
    pdf = pikepdf.new()
    font = helvetica(pdf)
    made = {name: make(pdf) for name, make in (fonts or {}).items()}
    if not isinstance(content, bytes):
        content = b"\n".join(content)
    page = pdf.make_indirect(Dictionary(
        Type=Name.Page, MediaBox=Array([0, 0, 612, 792]), Resources=Dictionary(Font=Dictionary(F1=font, **made)),
        Contents=pdf.make_stream(content)))
    pdf.Root.Pages = pdf.make_indirect(Dictionary(Type=Name.Pages, Kids=Array([page]), Count=1))
    page.Parent = pdf.Root.Pages
    if path is not None:
        pdf.save(path)
    return pdf, page


def integrity_errors(path):
    """The integrity check's findings of severity "error" for the file."""
    with pikepdf.open(path) as pdf:
        findings, _ = check_integrity(pdf)
    return [f for f in findings if f.severity == "error"]


def remediate(tmp_path, src, stem="doc", **opts):
    """A whole run of `src` with the mock provider, as a job under tmp_path/jobs; returns (result, store)."""
    from app.pipeline.orchestrator import JobOptions, run_remediation
    from app.storage import JobStore

    jid = str(uuid.uuid4())
    store = JobStore(jid, root=tmp_path / "jobs")
    store.create()
    shutil.copyfile(src, store.input_pdf)
    result = asyncio.run(run_remediation(store, JobOptions(stem=stem, job_id=jid, provider="mock", **opts)))
    return result, store


def same_render(a, b) -> bool:
    """The two files have as many pages and every page renders to the same pixels (72 dpi)."""
    with fitz.open(a) as da, fitz.open(b) as db:
        return da.page_count == db.page_count and all(
            da[i].get_pixmap(dpi=72).samples == db[i].get_pixmap(dpi=72).samples for i in range(da.page_count))
