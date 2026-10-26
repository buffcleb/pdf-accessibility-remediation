"""A table span the AI returns is clamped to the table before anything is built from it. A model (or an
instruction planted in the document) answering a span of millions used to have the engine enumerate every
covered position first."""
import asyncio
import json

import pymupdf as fitz

from app.ai.base import ChatResult, TokenUsage
from app.ai.mock import MockProvider
from app.pipeline.enrichment import EnrichmentStats, _tables
from app.pipeline.models import DocumentAnalysis, TableCell, TableRegion
from tests.engine.layout import document, page

ABSURD = 5_000_000


class _WideSpan(MockProvider):
    async def _chat(self, model, system, prompt, images):
        out = {"header_rows": 1, "header_cols": 0, "summary": "A table.", "is_layout_table": False,
               "spans": [{"row": 1, "col": 0, "row_span": ABSURD, "col_span": 2}]}
        return ChatResult(json.dumps(out), TokenUsage())


def _analysis() -> DocumentAnalysis:
    cells = [TableCell(row=r, col=c, bbox=(50.0 + 100 * c, 50.0 + 20 * r, 150.0 + 100 * c, 70.0 + 20 * r),
                       text="Head" if r == 0 else ("" if (r, c) != (1, 0) else "Only"))
             for r in range(3) for c in range(2)]
    table = TableRegion(id="p0t0", page=0, bbox=(50.0, 50.0, 250.0, 110.0), n_rows=3, n_cols=2, cells=cells)
    return document(page(table))


def test_a_span_past_the_table_is_clamped_to_it(tmp_path):
    doc = fitz.open()
    doc.new_page(width=612, height=792)
    doc.save(tmp_path / "blank.pdf")
    analysis = _analysis()

    async def run():
        ai = _WideSpan()
        async with ai:
            with fitz.open(tmp_path / "blank.pdf") as d:
                await _tables(ai, d, analysis, EnrichmentStats(), 100)

    asyncio.run(run())
    cell = next(c for c in analysis.pages[0].tables[0].cells if (c.row, c.col) == (1, 0))
    assert (cell.row_span, cell.col_span) == (2, 2)  # the merge covers the empty cells below and beside it, no more
