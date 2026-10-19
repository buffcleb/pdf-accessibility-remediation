"""A limit that cuts a result short says so in the report: a capped result mustn't pass for a complete one."""
import asyncio

import pymupdf as fitz

from app.ai.mock import MockProvider
from app.pipeline import enrichment, music
from app.pipeline.enrichment import EnrichmentStats, enrich
from app.pipeline.orchestrator import prepare_analysis
from app.config import get_settings


def _pictures(path, n):
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40), False)
    for k in range(n):
        pix.set_rect(pix.irect, (10 + 40 * k, 90, 200 - 30 * k))   # each its own picture
        page.insert_image(fitz.Rect(72 + 110 * (k % 4), 100 + 110 * (k // 4), 152 + 110 * (k % 4), 180 + 110 * (k // 4)), pixmap=pix)
    doc.save(path)
    return path


def test_figures_beyond_the_limit_are_reported(tmp_path, monkeypatch):
    src = _pictures(tmp_path / "p.pdf", 5)
    monkeypatch.setattr(get_settings(), "ai_images_max", 3, raising=False)
    analysis = prepare_analysis(src)
    stats = asyncio.run(_run(src, analysis))
    assert stats.images_described == 3
    assert any("2 figure(s) beyond the limit of 3 (AI_IMAGES_MAX) weren't described" in f and "page 1" in f for f in stats.findings)
    assert [(x["setting"], x["value"], x["wanted"]) for x in stats.limits_hit] == [("AI_IMAGES_MAX", 3, 5)]   # #312


async def _run(src, analysis):
    ai = MockProvider()
    async with ai:
        return await enrich(ai, src, analysis, stats=EnrichmentStats())


def test_music_beyond_the_limit_is_reported(tmp_path, monkeypatch):
    doc = fitz.open()
    for _ in range(3):
        page = doc.new_page(width=612, height=792)
        for top in (200, 400):
            for n in range(5):
                page.draw_line((150, top + n * 5), (450, top + n * 5), width=0.4)
    doc.save(tmp_path / "m.pdf")
    monkeypatch.setattr(get_settings(), "music_max_examples", 3, raising=False)
    monkeypatch.setattr(get_settings(), "music_max_score_lines", 3, raising=False)
    notes: list[str] = []
    stats = EnrichmentStats()
    assert music.apply(tmp_path / "m.pdf", prepare_analysis(tmp_path / "m.pdf"), [], notes=notes, limits=stats) == 3
    assert [(x["setting"], x["value"], x["wanted"]) for x in stats.limits_hit] == [("MUSIC_MAX_SCORE_LINES", 3, 6)]
    assert len(notes) == 1 and "3 line(s) of music beyond the limit of 3" in notes[0] and "from page 2 on" in notes[0]


def test_a_thousand_figures_are_described_by_default():
    """A 348-page test bank with a structure diagram per question has 379 figures: the old built-in limit of 300
    left 79 without alt text, and the document failed for it."""
    from app.config import Settings

    assert Settings().ai_images_max == 1000


def _stats_for(step, analysis_pages, tmp_path):
    """Run one AI step on made-up pages with a stand-in AI that counts what it is asked."""
    from app.pipeline.models import DocumentAnalysis

    doc = fitz.open()
    for _ in analysis_pages:
        doc.new_page(width=612, height=792)
    analysis = DocumentAnalysis(page_count=len(analysis_pages), title=None, language=None, has_struct_tree=False,
                                is_marked=False, pages=analysis_pages)

    class Counting:
        tables = order = 0

        async def analyze_table(self, png, page, grid):
            Counting.tables += 1
            return {"is_layout_table": False, "header_rows": 1, "header_cols": 0, "caption": None, "summary": None, "spans": []}

        async def determine_reading_order(self, png, page, width, height, regions):
            Counting.order += 1
            return [r["id"] for r in regions]

    stats = EnrichmentStats()
    asyncio.run(step(Counting(), doc, analysis, stats))
    return stats, Counting


def _page(n, *, tables=0, columns=1):
    from app.pipeline.models import PageAnalysis, TableCell, TableRegion, TextBlock

    blocks = [TextBlock(id=f"p{n}b{i}", page=n, bbox=(72, 100 + 30 * i, 300, 120 + 30 * i), text=f"Block {i}", font_size=11)
              for i in range(2)]
    made = [TableRegion(id=f"p{n}t{i}", page=n, bbox=(72, 300 + 60 * i, 400, 350 + 60 * i), n_rows=2, n_cols=1,
                        cells=[TableCell(row=r, col=0, bbox=None, text=f"cell {r}") for r in range(2)]) for i in range(tables)]
    page = PageAnalysis(number=n, width=612, height=792, rotation=0, pdf_to_page=(1, 0, 0, -1, 0, 792), blocks=blocks, tables=made)
    page.columns = columns
    page.reading_order = [b.id for b in blocks] + [t.id for t in made]
    return page


def test_tables_beyond_the_limit_are_reported_and_the_limit_is_a_setting(tmp_path, monkeypatch):
    """The limit of 100 was built into the code: a long report with more tables needed a new build to have them
    all looked at."""
    monkeypatch.setattr(get_settings(), "ai_tables_max", 2, raising=False)
    stats, asked = _stats_for(lambda ai, doc, a, s: enrichment._tables(ai, doc, a, s, 100), [_page(0, tables=2), _page(1, tables=3)], tmp_path)
    assert asked.tables == 2 and stats.tables_analyzed == 2
    assert any("3 table(s) beyond the limit of 2 (AI_TABLES_MAX)" in f and "from page 2 on" in f for f in stats.findings)
    assert [(x["setting"], x["value"], x["wanted"]) for x in stats.limits_hit] == [("AI_TABLES_MAX", 2, 5)]


def test_pages_beyond_the_reading_order_limit_are_reported_and_the_limit_is_a_setting(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_order_pages_max", 1, raising=False)
    stats, asked = _stats_for(enrichment._reading_order, [_page(n, columns=2) for n in range(3)], tmp_path)
    assert asked.order == 1 and stats.reading_order_pages == 1
    assert any("2 page(s) with columns beyond the limit of 1 (AI_ORDER_PAGES_MAX)" in f and "from page 2 on" in f
               for f in stats.findings)
    assert [(x["setting"], x["value"], x["wanted"]) for x in stats.limits_hit] == [("AI_ORDER_PAGES_MAX", 1, 3)]


def test_the_limits_keep_their_defaults():
    from app.config import Settings

    assert (Settings().ai_tables_max, Settings().ai_order_pages_max) == (100, 200)


def test_a_limit_met_once_per_page_counts_the_pages():
    stats = EnrichmentStats()
    for _ in range(3):
        stats.limit_hit("MAX_ORDER_REGIONS", 60, 1, "pages", count=True)
    stats.limit_hit("AI_NOTATION_MAX", 100, 140, "regions")
    stats.limit_hit("AI_NOTATION_MAX", 100, 120, "regions")       # the same limit again: the larger want
    assert [(x["setting"], x["wanted"]) for x in stats.limits_hit] == [("MAX_ORDER_REGIONS", 3), ("AI_NOTATION_MAX", 140)]


def test_the_tag_reviews_limits_and_the_findings_list_are_gathered_too():
    from app.pipeline import limits

    verification = {
        "primary": {"ai": {"capped_at": 150, "figures_selected": 200, "pages_selected": 10},
                    "skipped": [f"semantic checks limited to the first {get_settings().verify_max_pages} pages"]},
        "visual_fallback": {"ai": {"capped_at": 150, "figures_selected": 180, "pages_selected": 10}, "skipped": []},
    }
    got = {x["setting"]: x for x in limits.collect([{"setting": "AI_IMAGES_MAX", "value": 3, "wanted": 5, "effect": "e"}],
                                                   verification, ["f"] * 130, 900)}
    assert set(got) == {"AI_IMAGES_MAX", "VERIFY_AI_MAX_ITEMS", "VERIFY_MAX_PAGES", "FINDINGS_KEPT"}
    assert (got["VERIFY_AI_MAX_ITEMS"]["value"], got["VERIFY_AI_MAX_ITEMS"]["wanted"]) == (150, 210)
    assert got["VERIFY_MAX_PAGES"]["wanted"] == 900 and got["FINDINGS_KEPT"]["wanted"] == 130
    assert limits.collect([], None, [], 3) == []



def test_the_documents_reports_say_which_limits_it_reached():
    from app.pipeline.limits import describe
    from app.reports.markdown import render_job_markdown
    from app.reports.pdf import build_html

    hits = [{"setting": "AI_NOTATION_MAX", "value": 100, "wanted": 140, "effect": "regions of specialist notation kept the "
             "layout rules' reading"},
            {"setting": "MAX_ORDER_REGIONS", "value": 60, "wanted": 3, "count": True,
             "effect": "pages had too many regions for an AI reading order and kept the layout rules'"}]
    assert describe(hits[0]) == "40 regions of specialist notation kept the layout rules' reading (AI_NOTATION_MAX, set to 100)"
    assert describe(hits[1], with_setting=False).startswith("3 pages had too many regions")
    result = {"metrics": {"limits_hit": hits, "pages_processed": 9}, "reports": {}, "files": {}}
    md = render_job_markdown("j", {"filename": "a.pdf"}, "completed", result=result, ref=lambda n: n)
    assert "## Limits reached" in md and "| AI_NOTATION_MAX | 100 | 140 |" in md
    html = build_html({"filename": "a.pdf"}, result)
    assert "Limits reached" in html and "40 regions of specialist notation" in html and "AI_NOTATION_MAX" not in html
