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
_ACCENTS = re.compile("[̀-ͯ⃐-⃿]|(?<![A-Za-z])[¯ˉˆ˜][A-Za-z](?![A-Za-z])")  # combining marks, or a bar, hat or tilde set before its letter
_CHEM_ARROWS = set("⇌⇋⇄⇆⟶⟵⟷→←↔⇀⇁")
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
# The same in capitals without dots, as clinical documents write them. "PO" is also a post office box and a purchase
# order: it counts beside a dose or another of these.
_CAPITAL_RX = re.compile(r"(?<![A-Za-z.])(?:BID|TID|QID|PRN|QHS|QOD|QD|Q\s?\d+\s?H)(?![A-Za-z])")
_PO = ("(?:(?:" + "|".join(rf"(?<=\b{u}\s)" for u in (
    "mg", "mcg", "g", "mL", "ml", "mEq", "units", "tablets", "tablet", "tabs", "tab", "capsules", "capsule"))
    + r")PO\b|\bPO(?=\s(?:BID|TID|QID|PRN|QHS|QOD|QD|[qQ]\s?\d|daily|once|twice|every)))")
# Norms, sets, logic and order relations: research-level math the layout rules read symbol by symbol
_ADVANCED = set("∥‖∈∉∋⊂⊃⊆⊇⊊⊋∀∃∄⇒⇔⟹⟺⟸≲≳≪≫∼≃≡⊢⊨∘↦∧∨¬∩∪∅")
_GREEK_CHARS = re.compile("[\u0391-\u03a9\u03b1-\u03c9\u03d1\u03d5\u03f5]")
_ENGINEERING = set("ΩΩ℧µμ∠⊥∥∇∂∓≅∝⊕⊗⌀°")


# An unreadable character standing where a list mark stands: first in the block, or after the end of a sentence
_LIST_MARK = re.compile("(?:^|(?<=[.;:!?)])\\s)\\s*[\ufffd\ue000-\uf8ff\U000f0000-\U000ffffd]{1,2}(?=\\s)")
# A formula with what belongs to it: a charge, a state. And what joins formulas into one reaction.
_FORMULA_SPAN = re.compile(r"(?<![A-Za-z\d])\d*((?:\(?[A-Z][a-z]?\d*\)?\d*){1,})(\d?[+\-−–](?![A-Za-z\d]))?(\s?\((?:aq|g|l|s)\))?")
_REACTION_GLUE = re.compile("[\\s+=→←↔⇌⇋⇄⟶↑↓à\ufffd\ue000-\uf8ff]*")  # "à" and private-use: an arrow from a symbol font
_ARROW = re.compile("[→←↔⇌⇋⇄⟶]")
_REACTION_SIGN = re.compile("[+=→←↔⇌⇋⇄⟶à\ufffd\ue000-\uf8ff]")


@dataclass
class NotationNeed:
    kind: str  # math | chemistry | medical | engineering | unreadable
    reason: str


# A name with a number, not a formula: V followed by capitals ("VCC2", "VIN1": a supply or signal on a circuit), or
# the same capital twice running ("VSS", "ICC": a formula would write a count)
_NOT_A_FORMULA = re.compile(r"V[A-Z]{2,}\d*|[A-Z]*([A-Z])\1[A-Z]*\d*")


def _is_chemistry(text: str) -> bool:
    """A chemical formula with counts (H2SO4, C6H12O6, 2H2O), an ion with its charge (Fe2+), or a reaction between
    things written like formulas (N2 + O2 ↔ 2NO). An arrow alone isn't chemistry: math uses arrows too."""
    return bool(spans(text, "chemistry"))


def spans(text: str, kind: str) -> list[tuple[int, int]]:
    """Where the notation is inside a sentence: the formulas of a chemistry block (formulas joined by plus signs
    and arrows are one reaction), the doses and prescription shorthand of a medical one. Empty for other kinds."""
    found: list[tuple[int, int]] = []
    if kind == "unreadable":
        # An arrow from a symbol font inside a reaction: the reaction is the thing to read, if every unreadable
        # character of the block is in one
        found = spans(text, "chemistry")
        if all(any(a <= m.start() < b for a, b in found) for m in _UNREADABLE.finditer(text)):
            return found
        return []
    if kind == "chemistry":
        # Anything written like a formula: element symbols with counts, a charge or a state. Whether it is one is
        # decided for the chain it stands in: "Co + NO3– + H+ → NO + H2O" is a reaction because NO3 and H2O are
        # formulas beyond doubt, and then Co, H+ and NO are part of it.
        chains: list[list] = []
        for m in _FORMULA_SPAN.finditer(text):
            symbols = re.findall(r"[A-Z][a-z]?", m.group(1))
            if not symbols or not all(s in _ELEMENTS for s in symbols):
                continue
            if _NOT_A_FORMULA.fullmatch(m.group(1)) or re.search(r"[A-Za-z)]1(?!\d)", m.group(1)):
                continue  # a count of one is never written in a formula: "ON1" is a pin, not a compound
            sure = (len(symbols) >= 2 and bool(re.search(r"\d", m.group(1)))) or bool(
                m.group(2) and re.search(r"\d", m.group(1) + m.group(2)))  # an ion with its charge: Fe2+, SO42-
            marked = sure or bool(re.search(r"\d", m.group(1))) or bool(m.group(2)) or bool(m.group(3))
            between = text[chains[-1][-1][1]:m.start()] if chains else ""
            if chains and _REACTION_GLUE.fullmatch(between) and _REACTION_SIGN.search(between):
                chains[-1].append((m.start(), m.end(), sure, marked))
            else:
                chains.append([(m.start(), m.end(), sure, marked)])
        for chain in chains:
            # A formula beyond doubt, or a reaction: two or more things written like formulas with an arrow between
            reaction = sum(1 for c in chain if c[3]) >= 2 and any(
                _ARROW.search(text[x[1]:y[0]]) for x, y in zip(chain, chain[1:]))
            if not any(sure for _, _, sure, _ in chain) and not reaction:
                continue
            # A chain doesn't start or end on a bare word that happens to be element symbols ("In", "As")
            while chain and not chain[0][3]:
                chain.pop(0)
            while chain and not chain[-1][3]:
                chain.pop()
            if chain:
                found.append((chain[0][0], chain[-1][1]))
    elif kind == "medical":
        found = sorted((m.start(), m.end()) for rx in (_DOSAGE, _LATIN_RX) for m in rx.finditer(text))
        merged: list[tuple[int, int]] = []
        for start, end in found:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
            else:
                merged.append((start, end))
        found = merged
    return [(a, b) for a, b in found if text[a:b].strip()]


def place_in_reading(text: str, reading: str, start: int, end: int) -> tuple[int, int]:
    """Where a stretch of a block's text (start to end) lies in the layout rules' reading of that block, which is
    the text with words put in ("HNO3" as "HNO sub 3", "1023" as "10 to the power of 23")."""
    import difflib

    a = b = None
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, text, reading, autojunk=False).get_opcodes():
        if a is None and i2 > start:
            a = j1 + (start - i1 if tag == "equal" else 0)
        if i1 < end <= i2:
            b = j1 + (end - i1) if tag == "equal" else j2
        elif i1 >= end and b is None:
            b = j1
        if a is not None and b is not None:
            break
    return (a if a is not None else len(reading)), (b if b is not None else len(reading))


def in_a_sentence(text: str, found: list[tuple[int, int]]) -> bool:
    """Whether the notation is a small part of running text (so only it should be read by the AI), not the
    whole of the block."""
    covered = sum(b - a for a, b in found)
    return bool(found) and len(text) >= 30 and covered <= 0.6 * len(text)


# OCR text of a scanned formula: few ordinary words, many single characters, operators and brackets
_OPERATORS = set("=+-−×*/^()[]{}|<>∑∫√≤≥≈~")


def scanned_math(block: TextBlock) -> bool:
    """OCR text that looks like a formula the OCR mangled: few ordinary words, a quarter or more of single characters,
    operators and brackets in 15% or more of it, and read with low confidence. Measured on a scanned homework of
    typeset and handwritten equations against scanned prose, a recipe and sheet music: the operators are what keep
    the prose, the recipe's quantities and the music's stray letters out. Without OCR confidence, nothing is
    flagged: a scanned page then costs no extra AI call."""
    text = block.text or ""
    if block.ocr_conf is None or not 0 <= block.ocr_conf < 85 or block.role == "Artifact":
        return False
    words = text.split()
    if len(words) < 2 or len(text) < 6:
        return False
    plain = sum(1 for w in words if re.fullmatch(r"[A-Za-z]{3,}[.,;:]?", w))
    single = sum(1 for w in words if len(re.sub(r"[^\w]", "", w)) <= 1)
    ops = sum(1 for c in text if c in _OPERATORS)
    return plain <= 0.25 * len(words) and single >= 0.25 * len(words) and ops >= 0.15 * len(text)


def need(block: TextBlock) -> NotationNeed | None:
    """Why the layout rules' reading of this block is unreliable, or None if it's fine."""
    text = block.text or ""
    # A formula on a scanned page: its OCR text says little (the AI found it there, or it looks mangled). A tall one
    # can be taken for a heading by its size, so any role but an artifact counts
    if block.role != "Artifact" and text.strip() and (block.scan_formula or scanned_math(block)):
        return NotationNeed("math", "a formula on a scanned page" + (", found by the AI" if block.scan_formula
                                                                     else ", its OCR text garbled"))
    if block.role not in ("Formula", "P", "LBody", "LI", "TD", "TH", "Caption") or not text.strip():
        return None
    if len(text) > MAX_CHARS:
        # A long block is prose, and the AI would rewrite too much of it. What is read piece by piece, or by rule,
        # has no such risk: the formulas in a long paragraph, the doses in one.
        if len(text) > 10 * MAX_CHARS or block.role == "Formula" or _UNREADABLE.search(text):
            return None
        if has_doses(text):
            return NotationNeed("medical", "dosage or prescription notation")
        if (block.has_scripts or any(c in _CHEM_ARROWS for c in text)) and in_a_sentence(text, spans(text, "chemistry")):
            return NotationNeed("chemistry", "chemical formula or reaction")
        return None
    if n := len(_UNREADABLE.findall(text)):
        if len(text.strip()) <= 2:
            return None  # a lone symbol glyph (bullet, icon, piece of a bracket): nothing for the AI to read
        rest = _LIST_MARK.sub(" ", text)
        if (block.role != "Formula" and not _UNREADABLE.search(rest) and not re.search(r"[=≤≥<>←→+∑∫/^|]", rest)
                and (len(re.findall(r"[A-Za-z]{3,}", rest)) >= 3
                     or (1 <= len(rest.split()) <= 3 and all(re.fullmatch(r"[A-Za-z]{2,}[.,;:]?", w) for w in rest.split())))):
            # The marks of a bulleted list, drawn in a symbol font: the sentences read as they are. (A lost root
            # or summation sign also comes first in its line, but what follows it is math, not words.)
            return None
        return NotationNeed("unreadable", f"{n} character(s) with no readable meaning")
    if any(c in _BIG_OPERATORS for c in text):
        return NotationNeed("math", "sums, integrals or roots")
    if _ACCENTS.search(text):
        return NotationNeed("math", "accents over symbols (vectors, hats, bars)")
    if block.has_scripts and _is_chemistry(text):
        return NotationNeed("chemistry", "chemical formula or reaction")
    if any(c in _CHEM_ARROWS for c in text) and _is_chemistry(text):
        return NotationNeed("chemistry", "chemical reaction")
    if has_doses(text):
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


# Words a model writes into a transcription to say where limits go ("∑ from k = −∞ to ∞"). They print nothing.
_LIMIT_WORDS = ("from", "to", "of", "equals", "minus", "infinity")


def _strip_markup(transcription: str, page: str) -> str:
    out = re.sub(r"\\[A-Za-z]+", " ", _latex_letters(transcription))
    for w in _LIMIT_WORDS:
        if not re.search(rf"\b{w}\b", page):
            out = re.sub(rf"\b{w}\b", " ", out)
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


def consistent_scanned(ocr: str, transcription: str, latex: str | None = None) -> tuple[bool, str]:
    """The transcription check for a formula on a scanned page. OCR loses much of a formula, so the AI's reading can't
    be held to every character the OCR wrote; it must still be anchored to them: at least half of the letters and
    digits OCR read are in it, it is not many times longer than what OCR read, and it has a digit or an operator in
    it (a box around a word or a label isn't a formula)."""
    page = _symbols(_UNREADABLE.sub("", ocr))
    ai = _symbols(_strip_markup(transcription, ocr)) or _latex_symbols(latex or "")
    if not ai:
        return False, "the AI gave no transcription"
    if not re.search(r"[\d=+\-−×÷/^<>≤≥∫∑√∂]", transcription + (latex or "")):
        return False, "the AI's reading has no formula in it"  # a word or a label the box happened to hold
    total = sum(page.values())
    found = sum((page & ai).values())
    if total and found < 0.5 * total:
        return False, f"only {found} of the {total} characters OCR read are in the AI's reading"
    if sum(ai.values()) > 4 * max(total, 4) + 10:
        return False, "the AI's reading is many times longer than what OCR read there"
    return True, ""


# --- checking what will be spoken ----------------------------------------------------------------

_NUMBER = re.compile(r"\d+(?:\.\d+)?|\.\d+")
_THOUSANDS = re.compile(r"(?<![\d.,])(\d{1,3}(?:,\d{3})+)(?![\d])")
_UNITS_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
                "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS_WORDS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
               "ninety": 90}
# Words that stand for a number written on the page as a digit: "x squared" for x², "one half" for 1/2
_POWER_WORDS = {"squared": 2, "cubed": 3, "half": 2, "halves": 2, "first": 1, "second": 2, "third": 3, "thirds": 3,
                "fourth": 4, "fourths": 4, "quarter": 4, "quarters": 4, "fifth": 5, "fifths": 5, "sixth": 6,
                "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "double": 2, "triple": 3, "di": 2, "tri": 3}
# A dosage unit as written, and how it may be said. A reading that turns one unit into another is the worst
# mistake this feature can make, so each unit on the page must be heard, as itself or as its name.
_UNIT_FORMS = {
    "mcg": r"mcg|µg|μg|ug|micrograms?", "mg": r"mg|milligrams?", "kg": r"kg|kilograms?", "ng": r"ng|nanograms?",
    "g": r"g|grams?", "ml": r"ml|millilit(?:er|re)s?", "dl": r"dl|decilit(?:er|re)s?", "l": r"l|lit(?:er|re)s?",
    "mmol": r"mmol|millimoles?", "mol": r"mol|moles?", "meq": r"meq|milliequivalents?",
    "iu": r"iu|international units?",
}
_UNIT_ALIASES = {"µg": "mcg", "μg": "mcg", "ug": "mcg"}
_UNIT_ON_PAGE = re.compile(r"(?:(?<=\d)\s?|(?<=/)\s?)(mcg|µg|μg|ug|mg|kg|ng|g|mL|ml|dL|dl|L|mmol|mol|mEq|meq|IU)(?![A-Za-z])")
_INVERSE_UNIT = re.compile(r"(?<=[A-Za-z])\s?[-−–⁻]\s?[1¹](?![\d.])")
_DIVISION_SAID = re.compile(r"\b(?:divided by|over)\b", re.IGNORECASE)
_DIVISION_WRITTEN = re.compile(r"[/÷∕⁄]|\bper\b|\bover\b|fraction", re.IGNORECASE)


def _numbers(text: str) -> Counter:
    """The numbers written in a text, each in one form (".010", "0.01" and "0.0100" are the same number)."""
    text = _THOUSANDS.sub(lambda m: m.group(1).replace(",", ""), unicodedata.normalize("NFKC", text))
    out: Counter = Counter()
    for n in _NUMBER.findall(text):
        whole, _, frac = n.partition(".")
        out[(whole.lstrip("0") or "0") + ("." + frac.rstrip("0") if frac.rstrip("0") else "")] += 1
    return out


def _spoken_numbers(spoken: str) -> Counter:
    """The numbers heard in a reading: digits, and numbers in words ("two", "twenty five", "one hundred forty",
    "zero point three", "squared")."""
    words = re.findall(r"[A-Za-z]+|\d+(?:\.\d+)?|\.\d+", _THOUSANDS.sub(
        lambda m: m.group(1).replace(",", ""), unicodedata.normalize("NFKC", spoken)))
    low = [w.lower() for w in words]

    def small(i: int) -> tuple[int | None, int]:
        """A number under a hundred in words starting at i, and how many words it took."""
        if i < len(low) and low[i] in _TENS_WORDS:
            if i + 1 < len(low) and 0 < _UNITS_WORDS.get(low[i + 1], 0) < 10:
                return _TENS_WORDS[low[i]] + _UNITS_WORDS[low[i + 1]], 2
            return _TENS_WORDS[low[i]], 1
        if i < len(low) and low[i] in _UNITS_WORDS:
            return _UNITS_WORDS[low[i]], 1
        return None, 0

    out: Counter = Counter()
    i = 0
    while i < len(low):
        value, used = small(i)
        if value is None:
            if low[i] in _POWER_WORDS:
                out[str(_POWER_WORDS[low[i]])] += 1
            i += 1
            continue
        i += used
        if i < len(low) and low[i] == "hundred":
            i += 1
            if i < len(low) and low[i] == "and":
                i += 1
            rest, used = small(i)
            value = value * 100 + (rest or 0)
            i += used
        text = str(value)
        if i + 1 < len(low) and low[i] == "point":
            digits, j = "", i + 1
            while j < len(low) and (low[j] in _UNITS_WORDS and _UNITS_WORDS[low[j]] < 10 or low[j].isdigit()):
                digits += str(_UNITS_WORDS[low[j]]) if low[j] in _UNITS_WORDS else low[j]
                j += 1
            if digits:
                text, i = f"{value}.{digits}", j
        out.update(_numbers(text))
    out.update(_numbers(spoken))
    return out


DIVISION_NOT_WRITTEN = "the AI's spoken reading has a division that isn't written on the page"


def rejection_reason(problem: str) -> str:
    """A rejection's reason as one kind, for counting: its numbers and quoted parts left out."""
    return re.sub(r"\s+", " ", re.sub(r"\d+(?:\.\d+)?", "N", re.sub(r"[\"“][^\"”]*[\"”]", "…", problem))).strip()[:100]


def drawn_fraction(page, bbox, font_size: float) -> bool:
    """Whether the page draws a fraction bar in this region (a line, a thin rectangle, or a thin picture): a stacked
    fraction's "over" is drawn, not a character, so
    the division check would refuse a right reading of it. Strict, so that a rule between two lines of other text (a
    name over its tagline, a heading's underline) isn't taken for one: a thin horizontal line no longer than about a
    dozen characters, with text right above and right below it (within about half a line), both lying within the
    line's length and roughly centered on it, the wider of them filling at least half of it, and of about the same
    size; the line standing alone (nothing drawn touching its ends, as an arrowhead, a dimension line's extension lines
    or a table's vertical rules do, and no line of the same length right above or below, as a table's rules are), and
    what's below it not wholly in parentheses (a drawing's dimension over the same in millimetres), neither part a phrase (two or more words of
    three letters or more: "Morgan Homes" over its tagline is shaped like a fraction, but isn't one). page: a PyMuPDF
    page."""
    import pymupdf as fitz

    em = max(font_size or 10.0, 4.0)
    region = fitz.Rect(bbox) + (-em, -em, em, em)
    # Every horizontal line of the page (x0, x1, y), and every other piece drawn (its box), for the guards below
    lines: list[tuple[float, float, float]] = []
    pieces: list[fitz.Rect] = []
    for d in page.get_drawings():
        for item in d.get("items", []):
            if item[0] == "l":
                a, b = item[1], item[2]
                box = fitz.Rect(min(a.x, b.x), min(a.y, b.y), max(a.x, b.x), max(a.y, b.y))
                if abs(a.y - b.y) <= 0.5:
                    lines.append((box.x0, box.x1, (a.y + b.y) / 2))
            elif item[0] == "re":
                box = fitz.Rect(item[1])
                if 0 < box.height <= max(1.5, 0.15 * em):
                    lines.append((box.x0, box.x1, (box.y0 + box.y1) / 2))
            else:  # curves, quads: the box of their points
                pts = [pt for pt in item[1:] if hasattr(pt, "x")]
                box = fitz.Rect(min(p.x for p in pts), min(p.y for p in pts), max(p.x for p in pts),
                                max(p.y for p in pts)) if pts else None
            if box is not None:
                pieces.append(box)
    # Some TeX converters draw a rule as a picture one pixel tall, stretched (an inline image): on a 531-page book, every
    # fraction bar was one
    for info in page.get_image_info():
        r = fitz.Rect(info["bbox"])
        if 0 < r.height <= max(1.5, 0.15 * em) and r.width >= 3 * r.height:
            lines.append((r.x0, r.x1, (r.y0 + r.y1) / 2))

    def alone(k: int, x0: float, x1: float, y: float) -> bool:
        """A fraction bar stands alone: nothing else drawn touches its ends (an arrowhead, a dimension line's
        extension lines, a table's vertical rules), and no other line of the same length sits right above or below it
        (a table's rules come in sets)."""
        ends = (fitz.Rect(x0 - 1, y - 1, x0 + 1, y + 1), fitz.Rect(x1 - 1, y - 1, x1 + 1, y + 1))
        for box in pieces:
            if abs(box.x0 - x0) < 0.5 and abs(box.x1 - x1) < 0.5 and abs((box.y0 + box.y1) / 2 - y) < 0.5:
                continue  # the bar itself
            if any(not (box.x1 < e.x0 or box.x0 > e.x1 or box.y1 < e.y0 or box.y0 > e.y1) for e in ends):
                return False
        return not any(j != k and abs(a - x0) <= 1 and abs(b - x1) <= 1 and 0.5 < abs(yy - y) <= 30 * em
                       for j, (a, b, yy) in enumerate(lines))

    bars = [(k, x0, x1, y) for k, (x0, x1, y) in enumerate(lines)
            if region.x0 <= (x0 + x1) / 2 <= region.x1 and region.y0 <= y <= region.y1]
    if not bars:
        return False
    spans = [(fitz.Rect(sp["bbox"]), sp["size"], sp["text"]) for bl in page.get_text("dict", clip=region)["blocks"]
             for ln in bl.get("lines", []) for sp in ln["spans"] if sp["text"].strip()]
    for k, x0, x1, y in bars:
        w = x1 - x0
        if not 0.3 * em <= w <= 12 * em or not alone(k, x0, x1, y):
            continue
        over = [(r, sz, t) for r, sz, t in spans if y - 0.8 * em <= r.y1 <= y + 0.5 and r.x1 > x0 and r.x0 < x1]
        under = [(r, sz, t) for r, sz, t in spans if y - 0.5 <= r.y0 <= y + 0.8 * em and r.x1 > x0 and r.x0 < x1]
        if not over or not under:
            continue
        ok = True
        sizes, widths = [], []
        for part in (over, under):
            px0, px1 = min(r.x0 for r, _, _ in part), max(r.x1 for r, _, _ in part)
            pw = px1 - px0
            if px0 < x0 - 0.5 * em or px1 > x1 + 0.5 * em or abs((px0 + px1) / 2 - (x0 + x1) / 2) > 0.35 * w:
                ok = False
            widths.append(pw)
            if len(re.findall(r"[A-Za-z]{3,}", " ".join(t for _, _, t in part))) >= 2:
                ok = False  # a phrase, not a numerator or denominator
            sizes.append(max(sz for _, sz, _ in part))
        below = re.sub(r"\s+", "", " ".join(t for _, _, t in sorted(under, key=lambda u: u[0].x0)))
        if re.fullmatch(r"\([^()]*\)", below):
            ok = False  # a drawing's dimension in other units, "(0.406 – 1.270)": the bar groups a denominator already
        # The bar is as long as the wider part (a rule across a column is far longer than what's above and below it)
        if ok and max(widths) >= 0.5 * w and min(sizes) >= 0.7 * max(sizes):
            return True
    return False


def spoken_consistent(extracted: str, spoken: str, rules_reading: str = "", fraction_drawn: bool = False
                      ) -> tuple[bool, str]:
    """Whether what will be read aloud still says what the page says. The transcription is compared with the
    page character by character; the spoken form is free text, so this looks at what must never change:

    - every number on the page is heard (a dropped list item or answer takes its numbers with it);
    - every dosage unit on the page is heard, as written or by its name (never one unit for another);
    - letters spelled out one by one (a formula read "H O C H 3") don't outnumber the page's;
    - a division is heard only where one is written (as a character, or as a fraction bar the page draws:
      fraction_drawn, from drawn_fraction).

    There is no tolerance: a reading that fails keeps the layout rules' reading instead."""
    page_numbers, heard = _numbers(extracted), _spoken_numbers(spoken)
    # "mol-1", "s−1": a unit to the power of minus one is said "per", and its 1 isn't heard as a number
    per = min(len(_INVERSE_UNIT.findall(extracted)), len(re.findall(r"\bper\b", spoken, re.IGNORECASE)))
    missing = page_numbers - heard - Counter({"1": per})
    if missing:
        n = sorted(missing.elements())
        return False, f"the number{'s' if len(n) > 1 else ''} {', '.join(n[:5])} on the page {'are' if len(n) > 1 else 'is'} not in the AI's spoken reading"
    low = spoken.lower()
    units = Counter(_UNIT_ALIASES.get(u.lower(), u.lower()) for u in _UNIT_ON_PAGE.findall(extracted))
    for unit, count in units.items():
        said = len(re.findall(rf"(?<![a-zµμ])(?:{_UNIT_FORMS[unit]})(?![a-z])", low))
        if said < count:
            return False, f"the unit {unit} is on the page {count} time(s) and in the AI's spoken reading {said}"
    spelled = Counter(w for w in re.findall(r"(?<![A-Za-z])[A-Z](?![A-Za-z])", spoken))
    if sum(spelled.values()) >= 3:
        on_page = Counter(c for c in unicodedata.normalize("NFKC", extracted) if c.isupper())
        extra = spelled - on_page
        if extra:
            return False, f"the AI's spoken reading spells out {', '.join(sorted(extra.elements())[:5])} more often than the page has it"
    if _DIVISION_SAID.search(spoken) and not fraction_drawn and \
            not _DIVISION_WRITTEN.search(extracted + " " + (rules_reading or "")):
        return False, DIVISION_NOT_WRITTEN
    return True, ""


# --- one way of saying things ---------------------------------------------------------------------

_UNIT_NAMES = {"mcg": "microgram", "µg": "microgram", "μg": "microgram", "ug": "microgram", "mg": "milligram",
               "g": "gram", "kg": "kilogram", "ng": "nanogram", "ml": "milliliter", "dl": "deciliter", "l": "liter",
               "mmol": "millimole", "mol": "mole", "meq": "milliequivalent", "iu": "international unit",
               "h": "hour", "hr": "hour", "hrs": "hour", "min": "minute", "d": "day", "gtt": "drop", "gtts": "drop",
               "lb": "pound", "lbs": "pound"}
_UNIT_TOKEN = r"mcg|MCG|µg|μg|ug|mg|MG|kg|KG|Kg|ng|g|mL|ml|ML|dL|dl|L|mmol|mol|mEq|meq|IU|hrs|hr|h|min|gtts|gtt|lbs|lb"
_AFTER_NUMBER = re.compile(rf"(?<![\w.])(\d+(?:[.,]\d+)?)\s?({_UNIT_TOKEN}|d)(?![A-Za-z\d])")
_AFTER_PER = re.compile(rf"\bper\s({_UNIT_TOKEN}|d|m2|m²)(?![A-Za-z\d])")
_SLASH = re.compile(rf"(?<=[A-Za-z\d²])\s?/\s?(?=(?:{_UNIT_TOKEN}|day|dose|week|d|m2|m²)(?![A-Za-z]))")
_UNIT_ALONE = re.compile(r"(?<![\w.])(mcg|mg|kg|KG|mL|ml|mmol|mEq|gtts|gtt)(?=\s?(?:per\b|/))")
# Prescription shorthand. Lower-case forms that are also words ("bid", "po") need their dots; capitals don't.
_SHORTHAND = [
    (re.compile(r"(?<![\w.])[qQ]\.?\s?(\d+)\s?(?:h|H|hr|hrs|hours?)\b"), lambda m: f"every {m.group(1)} hour{'s' if m.group(1) != '1' else ''}"),
    (re.compile(r"(?<![\w.])(?:b\.\s?i\.\s?d\.|BID\b)"), "twice a day"),
    (re.compile(r"(?<![\w.])(?:t\.\s?i\.\s?d\.|tid\b|TID\b)"), "three times a day"),
    (re.compile(r"(?<![\w.])(?:q\.\s?i\.\s?d\.|qid\b|QID\b)"), "four times a day"),
    (re.compile(r"(?<![\w.])(?:q\.\s?h\.\s?s\.|qhs\b|QHS\b)"), "at bedtime"),
    (re.compile(r"(?<![\w.])(?:q\.\s?o\.\s?d\.|QOD\b)"), "every other day"),
    (re.compile(r"(?<![\w.])(?:q\.\s?d\.|QD\b)"), "once a day"),
    (re.compile(r"(?<![\w.])(?:p\.\s?r\.\s?n\.|prn\b|PRN\b)"), "as needed"),
    (re.compile(r"(?<![\w.])p\.\s?o\.|" + _PO), "by mouth"),
]
_NUMBER_RANGE = re.compile(r"(?<![\w.,/-])(\d+(?:\.\d+)?)\s?(?:[–—‐‑-]|\u00ad)+\s?((?!0\d)\d+(?:\.\d+)?)(?![\d/-])")  # "555-0100" is a number, not a range


def join_broken_words(text: str) -> str:
    """A reading replaces the block's own text for a screen reader, so a word broken over two lines ("extrac- tion")
    must be whole in it. "pre- and post-test" keeps its hyphen."""
    return re.sub(r"(?<=[a-z])[-\u00ad‐]\s(?!(?:and|or|to)\b)(?=[a-z])", "", text)


def has_doses(text: str) -> bool:
    """Whether a text holds dosage or prescription notation."""
    return bool(_DOSAGE.search(text) or _LATIN_RX.search(text) or _CAPITAL_RX.search(text) or re.search(_PO, text))


def doses_in_cells(page) -> int:
    """Put the doses in a page's table cells in words, by rule (a dosing table is where most of them are). Returns
    how many cells changed."""
    changed = 0
    for table in page.tables:
        for cell in table.cells:
            text = cell.text or ""
            if not text.strip() or len(text) > MAX_CHARS or not has_doses(text):
                continue
            before = cell.actual_text or text
            said = join_broken_words(say_doses(before))
            if said != before and spoken_consistent(text, said, cell.actual_text or "")[0]:
                cell.actual_text = said
                changed += 1
    return changed


def say_doses(text: str) -> str:
    """Dosage text the way it is read aloud, by rule: units by name ("5 mg/mL" as "5 milligrams per milliliter"),
    prescription shorthand in words ("q 6 hr" as "every 6 hours", "PO TID" as "by mouth three times a day"), and a
    dash between two numbers as "to". Numbers and every other word are left exactly as they are."""
    out = _NUMBER_RANGE.sub(r"\1 to \2", text)
    for pattern, said in _SHORTHAND:
        out = pattern.sub(said, out)
    out = _SLASH.sub(" per ", out)

    def named(unit: str, amount: str | None) -> str:
        name = {"m2": "square meter", "m²": "square meter"}.get(unit) or _UNIT_NAMES[unit.lower()]
        try:
            one = amount is None or float(amount.replace(",", ".")) == 1
        except ValueError:
            one = False
        return name if one else (name + "s" if not name.endswith("unit") else name + "s")

    out = _AFTER_NUMBER.sub(lambda m: f"{m.group(1)} {named(m.group(2), m.group(1))}", out)
    out = _AFTER_PER.sub(lambda m: "per " + named(m.group(1), None), out)
    out = _UNIT_ALONE.sub(lambda m: named(m.group(1), "2"), out)
    return re.sub(r"\s{2,}", " ", out)


# A count said as a word, as a digit: the same words _spoken_numbers hears, up to twelve (a count in a formula)
_DIGIT_WORDS = {word: str(n) for word, n in _UNITS_WORDS.items() if n <= 12}
_STATES = {"g": "gas", "l": "liquid", "s": "solid", "aq": "aqueous"}
_SOLID_FORMULA = re.compile(r"(?<![A-Za-z\d])\d*(?:\(?[A-Z][a-z]?\d*\)?\d*){2,}(?![a-z])")


def say_chemistry(spoken: str) -> str:
    """One style for a chemical formula read aloud, whatever the AI chose this time: symbols letter by letter with
    counts as digits ("H 2 S O 4", not "H two S O four", "H sub 2 ..." or "H2SO4"), parentheses said, "plus" and
    "yields" for a reaction, "is in equilibrium with" for a reversible one, states by name."""
    out = unicodedata.normalize("NFKC", spoken)
    out = re.sub(r"\s?\((aq|g|l|s)\)", lambda m: " " + _STATES[m.group(1)], out)
    out = re.sub(r"\s*(?:⇌|⇋|⇄|↔|<-+>|<=+>)\s*|\s+(?:double arrow|reversible arrow)\s+", " is in equilibrium with ", out)
    out = re.sub(r"\s*(?:→|⟶|-+>)\s*|\s+(?:arrow|right arrow|gives|produces)\s+", " yields ", out)

    def spell(m: re.Match) -> str:
        symbols = re.findall(r"[A-Z][a-z]?", m.group(0))
        if not all(s in _ELEMENTS for s in symbols) or not re.search(r"\d|[A-Z][a-z]?[A-Z]", m.group(0)):
            return m.group(0)
        said = re.sub(r"\(", " open parenthesis ", m.group(0))
        said = re.sub(r"\)", " close parenthesis ", said)
        return " ".join(re.findall(r"open parenthesis|close parenthesis|\d+|[A-Za-z]", said))

    out = _SOLID_FORMULA.sub(spell, out)
    # A state the model spelled out as it would a formula: "open parenthesis a q close parenthesis"
    out = re.sub(r"[;,]?\s*open parenthes[ie]s[;,]?\s+(a q|aq|g|l|s)[;,]?\s+close parenthes[ie]s",
                 lambda m: " " + _STATES[m.group(1).replace(" ", "")], out)
    # One element with a count or a coefficient: "N2", "3H2", "2Fe"
    out = re.sub(r"(?<![A-Za-z\d])(\d*)([A-Z][a-z]?)(\d*)(?![A-Za-z\d])",
                 lambda m: " ".join(x for x in (m.group(1), " ".join(m.group(2)), m.group(3)) if x)
                 if m.group(2) in _ELEMENTS and (m.group(1) or m.group(3)) else m.group(0), out)
    out = re.sub(r"\s*\b(?:sub|subscript|superscript)\b\s*", " ", out)
    words = "|".join(_DIGIT_WORDS)
    # A two-letter symbol the AI left whole inside a spelled formula: "Hg two plus", "Zn B r 2"
    follows = rf"(?= (?:\d|[A-Z]\b|(?:{words}|plus|minus)\b))"
    for _ in range(4):
        out, n = re.subn(rf"(?<![A-Za-z])([A-Z])([a-z])\b{follows}",
                         lambda m: f"{m.group(1)} {m.group(2)}" if m.group(1) + m.group(2) in _ELEMENTS else m.group(0), out)
        if not n:
            break
    # A count said as a word after a spelled letter: "H two O"
    out = re.sub(rf"(?<=\b[A-Za-z] )({words})\b", lambda m: _DIGIT_WORDS[m.group(1)], out)
    out = re.sub(r"(?<=[A-Za-z\d]) \+ (?=[A-Za-z\d(])", " plus ", out)
    return re.sub(r"\s{2,}", " ", out).strip()


def one_style(spoken: str, kind: str) -> str:
    """The reading in this service's style for its kind of notation."""
    words = len(spoken.split())
    if words >= 6 and spoken.count(";") >= 0.5 * words:
        spoken = spoken.replace(";", "")  # a semicolon after every word: not statements, just a habit of the model
    spoken = re.sub(r";\s*(plus|minus|equals|arrow|yields)\s*;", r" \1 ", spoken)
    spoken = re.sub(r"\b(open|close) parentheses\b", r"\1 parenthesis", re.sub(r"\s{2,}", " ", spoken)).strip()
    if kind == "medical":
        return say_doses(spoken)
    if kind == "chemistry":
        return say_chemistry(spoken)
    return spoken


_LEAD_WORD = re.compile(r"^\s*(where|and|then|so|or|with|thus|hence|hence,|i\.e\.,?|for)\s+(?=\S)", re.IGNORECASE)
_EQUATION_LABEL = re.compile(r"\s*\((\d+(?:\.\d+)*[a-z]?)\)\s*$")


def around_an_equation(text: str) -> tuple[str, str, str]:
    """A displayed equation as extracted often carries a word before it ("where") and its number after it
    ("(2.16)"). A model reading the picture gives the equation and tends to leave both out, and then fails the
    comparison with the page. Returns (the word, the equation, its number); the first and last may be empty.
    Only something with an equals sign or a sum, integral or root in it is treated this way."""
    math = bool(re.search(r"[=∑∫∏√≤≥<>≈]", text))
    label = _EQUATION_LABEL.search(text)
    if label and not math and "." not in label.group(1):
        label = None  # "(2)" after words is a list number or a reference; "(2.16)" is an equation's number
    core = text[:label.start()] if label else text
    lead = _LEAD_WORD.match(core) if math else None
    if lead:
        core = core[lead.end():]
    if not core.strip():
        return "", text, ""
    return (lead.group(1) if lead else ""), core, (label.group(1) if label else "")


def with_its_surroundings(spoken: str, lead: str, label: str) -> str:
    """The reading with the word before the equation and the equation's number, if the model left them out."""
    out = spoken.strip()
    if lead and not re.match(rf"{re.escape(lead)}\b", out, re.IGNORECASE):
        out = f"{lead} {out}"
    if label and label not in out:
        out = f"{out.rstrip('.; ')}; equation {label}"
    return out


def readable(text: str) -> int:
    """Letters and digits a reader could make use of."""
    return sum(1 for c in _UNREADABLE.sub("", text) if c.isalnum())


def apply_reading(page, reading: dict) -> bool:
    """Put an AI reading back onto a re-analyzed page (the modified output, after flattening): the blocks
    inside its region become one block with the reading as its read-aloud text. Returns whether any
    block matched."""
    from app.pipeline.models import bbox_area, bbox_intersection, merge_blocks

    box = tuple(reading["bbox"])
    inside = [b for b in page.blocks if b.role != "Artifact"
              and bbox_intersection(b.bbox, box) >= 0.6 * max(1.0, bbox_area(b.bbox))]
    if not inside:
        return False
    inside.sort(key=lambda b: (round(b.bbox[1] / 4), b.bbox[0]))
    head = inside[0]
    merge_blocks(page, head, inside[1:])
    head.role = reading.get("role") or head.role
    head.actual_text = reading["spoken"]
    head.notation_reading = True
    return True
