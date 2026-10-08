"""A job's AI tier can bring its own review model, which replaces the default judge."""
import pytest

from app.verification import ai_review
from app.verification.ai_review import FigureItem, ReviewPlan


@pytest.mark.asyncio
async def test_review_uses_the_tiers_judge(monkeypatch, tmp_path):
    asked = []

    def fake_provider(name, model=None, **kw):
        asked.append((name, model))
        return None  # stop before any call is made

    monkeypatch.setattr(ai_review, "get_provider", fake_provider)
    import pymupdf as fitz
    doc = fitz.open()
    doc.new_page()
    doc.save(tmp_path / "x.pdf")
    plan = ReviewPlan(figures=[FigureItem(element=1, role="Figure", page=0, bbox=(0, 0, 10, 10), alt="x",
                                          context="", flagged=True)])
    await ai_review.review(tmp_path / "x.pdf", plan, full=True, seed="j", judge_model="cloud-judge")
    assert asked and asked[0] == ("gateway", "cloud-judge")
    asked.clear()
    await ai_review.review(tmp_path / "x.pdf", plan, full=True, seed="j")
    assert asked and asked[0][1] != "cloud-judge"  # the engine's default judge
