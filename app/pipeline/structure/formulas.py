"""Paragraphs that are mostly math: Formula."""
from __future__ import annotations

import re

from app.pipeline.models import DocumentAnalysis, TextBlock


_MATH_CHARS = set("=×÷±≤≥<>+−√∑∫∞≈≠·^")
_WORD = re.compile(r"[A-Za-z]{3,}")


def assign_formulas(doc: DocumentAnalysis) -> None:
    """A paragraph that is mostly math (operators, numbers, few words) is a Formula; its spoken form
    becomes the alt text. Prose that merely contains a little math stays a paragraph (with actual text
    for its powers)."""
    for p in doc.pages:
        for b in p.blocks:
            if b.role == "P" and mostly_math(b):
                b.role = "Formula"


def mostly_math(b: TextBlock) -> bool:
    """Math operators with numbers (or scripts, or a stacked fraction: "M = n over V"), and few words: a formula, not
    prose with a little math in it."""
    t = b.text
    dense = len(re.sub(r"\s", "", t)) or 1
    words = sum(len(w) for w in _WORD.findall(t))
    return any(c in _MATH_CHARS for c in t) and (b.has_scripts or b.fraction or any(c.isdigit() for c in t)) and words <= 0.25 * dense
