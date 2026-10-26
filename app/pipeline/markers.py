"""What list labels, bullets, footnote and table-note marks, note numbers, page numbers and captions look like: the
patterns the analyzer and the structure passes share (refactor plan item 6), in one place so a new bullet glyph or
label form is added once.

app/verification keeps its own copies, so the checker shares no code with what it checks.
"""
from __future__ import annotations

import re

# --------------------------------------------------------------------------- list labels and bullets
BULLET_CHARS = "•‣◦⁃∙▪●■·➢✓✔"
# A list item's start: a bullet, or a number, letter or roman numeral with "." or ")", then a space
BULLET = re.compile(r"^\s*([" + BULLET_CHARS + r"\-\*–]|\(?\d{1,3}[.)]|\(?[a-zA-Z][.)]|\(?[ivxlcIVXLC]{1,5}[.)])\s+")
BULLET_START = BULLET
BULLET_ONLY = re.compile(r"^\s*[" + BULLET_CHARS + r"\-\*–]\s*$")
# A list label standing alone as a run: (a)  a)  1.  (iv). The same label forms as BULLET, but anchored at both ends
# and without the space after: kept apart, as one reads a run and the other a line's start
LABEL_RUN = re.compile(r"^\s*\(?([a-zA-Z]|[ivxlcIVXLC]{1,5}|\d{1,3})[.)]\s*$")
# Labels that only lists use: in brackets ("(a)", "(1)", "(iv)"), or a lower-case letter or numeral ("a.", "iv)")
LIST_LABEL = re.compile(r"\(.+[.)]|[a-z][.)]|[ivxlc]{1,5}[.)]")

# --------------------------------------------------------------------------- footnote, endnote and table-note marks
# What a footnote mark looks like: a number of up to three digits, or up to three of the usual symbols
FOOTNOTE_MARK = re.compile(r"^(\d{1,3}|[*†‡§¶]{1,3})$")
# A footnote's own number or symbol, written at its start ("1 Smith", "2. See", "* Rates")
FOOTNOTE_LEAD = re.compile(r"^\s*(\d{1,3}|[*†‡§¶]{1,3})(?:[.)]\s*|\s+)(?=\S)")
# An endnote's written number at its start ("1. Smith", "1 Smith")
NOTE_NUMBER = re.compile(r"^\s*(\d{1,3})(?:[.)]\s*|\s+)(?=\S)")
# A table note's mark: one of the usual symbols, up to three of them; and a note under a table that starts with one
TABLE_MARK = re.compile(r"[*†‡§¶]{1,3}")
TABLE_NOTE = re.compile(r"^\s*([*†‡§¶]{1,3})\s*(?=\S)")
# How a footnote symbol is said
MARK_NAMES = {"*": "asterisk", "†": "dagger", "‡": "double dagger", "§": "section mark", "¶": "paragraph mark"}


def footnote_words(mark: str, kind: str = "footnote") -> str:
    """How a footnote mark is said: "footnote 1", "footnote asterisk"; an endnote's "endnote 1"."""
    return f"{kind} " + (mark if mark.isdigit() else " ".join(MARK_NAMES.get(c, c) for c in mark))


# --------------------------------------------------------------------------- page numbers
PAGE_NUM = re.compile(r"^\s*(page\s*)?\d{1,4}(\s*(of|/)\s*\d{1,4})?\s*$", re.I)

# --------------------------------------------------------------------------- captions
# Captions (#620). A Caption block (structure) starts with one of these words and a number: "Figure 3", "Table 2",
# "Scheme 1". Beside a drawing (a graph's lines, a reaction scheme) a line starting so is kept out of it, as is a
# worked example's or an equation's label: those are read as text, but aren't captions of their own
CAPTION = re.compile(r"^\s*(figure|fig\.|table|chart|exhibit|image|scheme|graph|plate)\s*\d", re.I)
TABLE_CAPTION = re.compile(r"^\s*table\b", re.I)  # a caption that pairs with a table
FIGURE_CAPTION = re.compile(r"^\s*(figure|fig\.|chart|image|scheme|graph|plate)", re.I)  # one that pairs with a figure
DRAWING_CAPTION = re.compile(
    r"^\s*(?:fig(?:ure)?\.?|table|chart|exhibit|image|scheme|graph|plate|example|eq(?:uation)?\.?)\s*\d", re.I)
