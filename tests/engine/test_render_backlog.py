"""Pictures for the AI are rendered right before their call, inside one of the provider's places, not all up front.
Rendered up front, every figure of a document (up to AI_IMAGES_MAX, a thousand) sat in memory while the calls
queued four at a time."""
import asyncio

import pymupdf as fitz

from app.ai.mock import MockProvider
from app.config import get_settings
from app.pipeline import enrichment
from app.pipeline.enrichment import EnrichmentStats, _images
from app.pipeline.models import DocumentAnalysis, ImageRegion, PageAnalysis


class _Backlog:
    """How many pictures had been rendered but not yet sent, seen from inside each call."""

    def __init__(self):
        self.rendered = self.sent = 0
        self.worst = 0


def _analysis(n: int) -> DocumentAnalysis:
    page = PageAnalysis(number=0, width=612, height=792, rotation=0, pdf_to_page=(1, 0, 0, -1, 0, 792))
    page.images = [ImageRegion(id=f"p0i{k}", page=0, bbox=(10.0 + 20 * k, 10.0, 25.0 + 20 * k, 25.0), xref=0)
                   for k in range(n)]
    return DocumentAnalysis(page_count=1, pages=[page], title=None, language=None, has_struct_tree=False, is_marked=False)


def test_renders_never_run_ahead_of_the_calls(tmp_path, monkeypatch):
    doc = fitz.open()
    doc.new_page(width=612, height=792)
    doc.save(tmp_path / "blank.pdf")
    backlog = _Backlog()
    original = enrichment.render_png

    def counting_render(*args, **kwargs):
        backlog.rendered += 1
        return original(*args, **kwargs)

    class Slow(MockProvider):
        async def _chat(self, model, system, prompt, images):
            backlog.sent += 1
            backlog.worst = max(backlog.worst, backlog.rendered - backlog.sent)
            await asyncio.sleep(0.02)
            return await super()._chat(model, system, prompt, images)

    monkeypatch.setattr(enrichment, "render_png", counting_render)
    monkeypatch.setattr(get_settings(), "ai_concurrency", 3, raising=False)

    async def run():
        ai = Slow()
        async with ai:
            with fitz.open(tmp_path / "blank.pdf") as d:
                await _images(ai, d, _analysis(12), EnrichmentStats(), 100)
        return ai

    ai = asyncio.run(run())
    assert ai.calls == 12 and backlog.rendered == 12
    assert backlog.worst == 0, f"{backlog.worst} picture(s) were rendered before the call that sent them had a place"
