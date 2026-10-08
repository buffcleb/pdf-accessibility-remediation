"""Specialist notation the layout rules can't read reliably, and the AI fallback for it.

Layout rules give math written as text its read-aloud form (powers, subscripts, fractions, separate
equations). Some notation is beyond them: characters with no Unicode meaning, big operators and
radicals, accents, chemistry, dosage notation, engineering units and symbols. For those blocks the AI
reads the rendered region. Its answer is only used if its transcription accounts for the characters
actually on the page; every reading it supplies is flagged for a person to check.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

from app.pipeline.models import TextBlock

MAX_CHARS = 400  # longer blocks are prose; the AI would rewrite too much

_UNREADABLE = re.compile("[�-\U000f0000-\U000ffffd]")
_BIG_OPERATORS = set("∑∏∐∫∬∭∮∯∰√∛∜⋀⋁⋂⋃⨁⨂")
_ACCENTS = re.compile("[̀-ͯ⃐-⃿]")
_CHEM_ARROWS = set("⇌⇋⇄⇆⟶⟵⟷→←↔⇀⇁")
# Two or more element symbols with counts, e.g. H2SO4, Ca(OH)2, C6H12O6 (as extracted: digits inline)
_CHEM_FORMULA = re.compile(r"\b(?:\(?[A-Z][a-z]?\d*\)?\d*){2,}\b")
_ELEMENTS = set("""H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br
Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta
W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am""".split())
_DOSAGE = re.compile(r"\b\d+(?:[.,]\d+)?\s?(?:mg|mcg|µg|μg|ng|g|mL|ml|L|mmol|mol|IU|units?|mEq)\s?/\s?"
                     r"(?:kg|m2|m²|L|dL|mL|day|d|h|hr|min|dose)\b", re.IGNORECASE)
# Prescription abbreviations. Two-letter ones need their dots ("p.o.", "i.v."), so roman numerals and
# ordinary words ("iv", "po") don't match; prescriptions are written in lower case, and a letter-and-dot
# before ("U.S.C.") means a longer abbreviation.
_LATIN_RX = re.compile(r"(?<![A-Za-z]\.)\b(?:(?:q\.?\s?i\.?\s?d|t\.?\s?i\.?\s?d|q\.?\s?\d+\s?h|p\.?\s?r\.?\s?n|q\.?\s?h\.?\s?s)\.?|"
                       r"(?:b\.\s?i\.\s?d|p\.\s?o|q\.\s?d|s\.\s?l|i\.\s?v|i\.\s?m|s\.\s?c)\.)(?=\s|$|[,;)])")
# Norms, sets, logic and order relations: research-level math the layout rules read symbol by symbol
_ADVANCED = set("∥‖∈∉∋⊂⊃⊆⊇⊊⊋∀∃∄⇒⇔⟹⟺⟸≲≳≪≫∼≃≡⊢⊨∘↦∧∨¬∩∪∅")
_GREEK_CHARS = re.compile("[\u0391-\u03a9\u03b1-\u03c9\u03d1\u03d5\u03f5]")
_ENGINEERING = set("ΩΩ℧µμ∠⊥∥∇∂∓≅∝⊕⊗⌀°")


@dataclass
class NotationNeed:
    kind: str  # math | chemistry | medical | engineering | unreadable
    reason: str


def _is_chemistry(text: str) -> bool:
    """A chemical formula with counts (H2SO4, C6H12O6): two or more element symbols and a digit.
    An arrow alone isn't chemistry: math uses arrows too."""
    for m in _CHEM_FORMULA.finditer(text):
        symbols = re.findall(r"[A-Z][a-z]?", m.group(0))
        if len(symbols) >= 2 and all(s in _ELEMENTS for s in symbols) and re.search(r"\d", m.group(0)):
            return True
    return False


def need(block: TextBlock) -> NotationNeed | None:
    """Why the layout rules' reading of this block is unreliable, or None if it's fine."""
    text = block.text or ""
    if block.role not in ("Formula", "P", "LBody", "LI", "TD", "TH", "Caption") or not text.strip():
        return None
    if len(text) > MAX_CHARS:
        return None
    if n := len(_UNREADABLE.findall(text)):
        if len(text.strip()) <= 2:
            return None  # a lone symbol glyph (bullet, icon, piece of a bracket): nothing for the AI to read
        return NotationNeed("unreadable", f"{n} character(s) with no readable meaning")
    if any(c in _BIG_OPERATORS for c in text):
        return NotationNeed("math", "sums, integrals or roots")
    if _ACCENTS.search(text):
        return NotationNeed("math", "accents over symbols (vectors, hats, bars)")
    if block.has_scripts and _is_chemistry(text):
        return NotationNeed("chemistry", "chemical formula or reaction")
    if any(c in _CHEM_ARROWS for c in text) and _is_chemistry(text):
        return NotationNeed("chemistry", "chemical reaction")
    if _DOSAGE.search(text) or _LATIN_RX.search(text):
        return NotationNeed("medical", "dosage or prescription notation")
    if block.role == "Formula" and any(c in _ADVANCED for c in text):
        return NotationNeed("math", "norms, sets, logic or order relations")
    spoken = block.actual_text or ""
    scripts = spoken.count(" sub ") + spoken.count("to the power of") + spoken.count(" squared") + spoken.count(" cubed")
    if block.role == "Formula" and scripts >= 2 and _GREEK_CHARS.search(text):
        return NotationNeed("math", "several sub- and superscripts with Greek letters")
    if block.role == "Formula" and any(c in _ENGINEERING for c in text):
        return NotationNeed("engineering", "units or engineering symbols")
    return None


# --- checking the AI's answer --------------------------------------------------------------------

def _symbols(text: str) -> Counter:
    """Letters and digits, folded to compare transcriptions with extracted text (case and width kept
    apart from layout differences: "H₂O" and "H2O" compare equal)."""
    out: Counter = Counter()
    for c in unicodedata.normalize("NFKC", text):
        if c.isalnum():
            out[c] += 1
    return out


def _latex_symbols(latex: str) -> Counter:
    """Letters and digits a LaTeX source would print: command names (\\frac, \\sqrt, \\mathrm) are not."""
    stripped = re.sub(r"\\[A-Za-z]+", " ", _latex_letters(latex or ""))
    return _symbols(stripped)


# LaTeX command names models slip into a plain transcription, sometimes without the backslash
# ("left(4a²/pright)"). They print nothing, so they don't count as characters.
_LATEX_WORDS = ("left", "right", "frac", "dfrac", "tfrac", "sqrt", "mathrm", "mathbf", "mathit", "mathcal",
                "mathbb", "text", "textrm", "operatorname", "cdot", "cdots", "ldots", "dots", "times", "bigl", "bigr",
                "Bigl", "Bigr", "big", "Big", "displaystyle", "limits", "quad", "qquad", "begin", "end", "pmatrix",
                "bmatrix", "matrix", "cases", "array", "ce")


_GREEK = dict(zip(
    "alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota kappa lambda mu nu xi pi varpi rho "
    "varrho sigma varsigma tau upsilon phi varphi chi psi omega Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi "
    "Psi Omega ell".split(),
    "αβγδεεζηθθικλμνξππρρσςτυφφχψωΓΔΘΛΞΠΣΥΦΨΩℓ"))


def _latex_letters(text: str) -> str:
    """Greek and other letter commands as the letters they print (\\sigma -> σ)."""
    return re.sub(r"\\([A-Za-z]+)", lambda m: _GREEK.get(m.group(1), m.group(0)), text)


def _strip_markup(transcription: str, page: str) -> str:
    out = re.sub(r"\\[A-Za-z]+", " ", _latex_letters(transcription))
    for w in _LATEX_WORDS:
        if w not in page:
            out = re.sub(rf"(?<![A-Za-z]){w}(?![a-z])", " ", out)
    return re.sub(r"[{}_^]", " ", out)


def consistent(extracted: str, transcription: str, latex: str | None = None) -> tuple[bool, str]:
    """Whether the AI's transcription accounts for what's on the page. Every readable letter and digit
    extracted must appear in it (allowing a little OCR-like slack), and it may add only about as much
    as the unreadable characters could stand for."""
    page = _symbols(_UNREADABLE.sub("", extracted))
    ai = _symbols(_strip_markup(transcription, extracted)) or _latex_symbols(latex or "")
    if not ai:
        return False, "the AI gave no transcription"
    total = sum(page.values())
    missing = sum((page - ai).values())
    extra = sum((ai - page).values())
    unreadable = len(_UNREADABLE.findall(extracted))
    if total and missing > max(1, 0.1 * total):
        return False, f"{missing} of {total} characters on the page are missing from the AI's reading"
    if extra > max(2, 0.15 * total) + 3 * unreadable:
        return False, f"the AI's reading has {extra} characters that aren't on the page"
    return True, ""


def readable(text: str) -> int:
    """Letters and digits a reader could make use of."""
    return sum(1 for c in _UNREADABLE.sub("", text) if c.isalnum())


def apply_reading(page, reading: dict) -> bool:
    """Put an AI reading back onto a re-analyzed page (the modified output, after flattening): the blocks
    inside its region become one block with the reading as its read-aloud text. Returns whether any
    block matched."""
    from app.pipeline.models import append_block, bbox_area, bbox_intersection

    box = tuple(reading["bbox"])
    inside = [b for b in page.blocks if b.role != "Artifact"
              and bbox_intersection(b.bbox, box) >= 0.6 * max(1.0, bbox_area(b.bbox))]
    if not inside:
        return False
    inside.sort(key=lambda b: (round(b.bbox[1] / 4), b.bbox[0]))
    head = inside[0]
    for b in inside[1:]:
        append_block(head, b)
        page.blocks.remove(b)
        if b.id in page.reading_order:
            page.reading_order.remove(b.id)
    head.role = reading.get("role") or head.role
    head.actual_text = reading["spoken"]
    return True
