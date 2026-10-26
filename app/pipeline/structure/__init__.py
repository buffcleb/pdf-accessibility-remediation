"""Heuristic logical structure: headings, lists, running headers/footers, columns, reading order.

assign_roles runs the passes in PASSES in order. The order is the behaviour: each pass names the ones that must have
run before it, and why, and the list is checked when the module loads.
"""
from __future__ import annotations

from typing import Callable

from app.pipeline.models import DocumentAnalysis
from app.pipeline.structure.artifacts import (mark_drawn_lines, mark_heading_icons, mark_repeated_images,
                                              mark_running_artifacts, mark_text_badges)
from app.pipeline.structure.formulas import assign_formulas
from app.pipeline.structure.headings import assign_headings
from app.pipeline.structure.lists_captions import assign_lists_and_captions, attach_list_continuations, caption_pairs
from app.pipeline.markers import footnote_words
from app.pipeline.structure.notes import mark_endnotes, mark_footnotes, mark_table_notes
from app.pipeline.structure.reading_order import (detect_columns, heuristic_reading_order, needs_ai_reading_order,
                                                  right_to_left, row_top)

__all__ = ["assign_roles", "PASSES", "caption_pairs", "footnote_words", "detect_columns", "heuristic_reading_order",
           "needs_ai_reading_order", "right_to_left", "row_top"]

# (pass, the passes that must have run before it)
PASSES: list[tuple[Callable[[DocumentAnalysis], None], tuple[str, ...]]] = [
    (mark_drawn_lines, ()),
    (mark_running_artifacts, ()),
    (mark_repeated_images, ()),
    # A row of dashes with a dot isn't a heading (#438); nor is a page number or a running header (skipped as Artifact)
    (assign_headings, ("mark_drawn_lines", "mark_running_artifacts")),
    (mark_heading_icons, ("assign_headings",)),  # an icon beside a heading: it reads H* roles
    (mark_text_badges, ()),
    # Only P is made a list item or a caption: a heading candidate with a bullet label was decided by assign_headings
    (assign_lists_and_captions, ("assign_headings",)),
    (attach_list_continuations, ("assign_lists_and_captions",)),  # joins lines to LI
    # Only P becomes a Formula; a row of dashes with a dot in it was taken for one (#438)
    (assign_formulas, ("mark_drawn_lines", "assign_lists_and_captions")),
    (mark_footnotes, ("assign_lists_and_captions",)),  # a footnote is a P or an LI
    # Only the marks no footnote claimed point at endnotes; an entry's number can be its list label. It orders the
    # pages with heuristic_reading_order itself, as page.reading_order isn't set until prepare_analysis
    (mark_endnotes, ("assign_lists_and_captions", "mark_footnotes")),
    (mark_table_notes, ("mark_footnotes",)),  # a mark a footnote claimed isn't a table's
]


def _check_order(passes: list) -> None:
    seen: set[str] = set()
    for fn, needs in passes:
        missing = [n for n in needs if n not in seen]
        if missing:
            raise RuntimeError(f"structure pass {fn.__name__} needs {', '.join(missing)} to run first")
        seen.add(fn.__name__)


_check_order(PASSES)


def assign_roles(doc: DocumentAnalysis) -> None:
    for fn, _ in PASSES:
        fn(doc)
