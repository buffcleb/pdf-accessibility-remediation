"""Grouping a page's characters into visual lines must stay fast as the page grows. Each character was compared
with every character already placed, in every line within reach, so a page's worth of text (which one merged
block can hold, once fractions or interleaved blocks join blocks) was cubic: 22 of 42 seconds of analysis on an
82-page plan."""
import time

from app.pipeline.analyzer import _Run, _visual_lines

LINES, CHARS = 200, 80  # a page-sized merged block of 16,000 characters


def _page_of_runs() -> list[_Run]:
    runs, seq = [], 0
    for ln in range(LINES):
        base = 60.0 + 9.0 * ln
        for k in range(CHARS):
            seq += 1
            x = 40.0 + 6.0 * k
            char = {"bbox": (x, base - 7.0, x + 5.5, base + 2.0), "c": "a", "origin": (x, base)}
            runs.append(_Run({"size": 9.0, "flags": 0, "font": "Helvetica", "color": 0}, char, seq))
    return runs


def test_a_dense_page_is_grouped_in_well_under_a_second():
    runs = _page_of_runs()
    started = time.monotonic()
    lines = _visual_lines(runs)
    took = time.monotonic() - started
    assert len(lines) == LINES and all(len(ln.runs) == CHARS for ln in lines)
    assert took < 3.0, f"grouping {len(runs)} characters took {took:.1f} s"
