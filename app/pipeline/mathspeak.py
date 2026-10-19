"""Formulas read by rule (#403): MathCAT's speech and braille for each formula's MathML, and the check of the AI's
spoken reading against it.

MathCAT (MIT, https://github.com/daisy/MathCAT) runs as one small program per document (`mathcat-cli`, built into
the engine image from docker/mathcat): MathML in, ClearSpeak speech and Nemeth and UEB braille out. Without the
program (a local run outside the image) nothing here happens and the formulas keep only the AI's reading.

The check: both readings must say the same numbers and the same letters (variables, Greek letters), each as often.
Words differ and that's fine ("over" or "divided by", "equals" or "is equal to"); a different number or variable
is not. MathCAT's reading comes from the AI's own LaTeX, so a disagreement means the AI's spoken reading and its
LaTeX don't say the same thing: one of them is wrong. Nothing is settled here: the formula is flagged, both readings
go to the person reviewing it (Edit tag), and every disagreement goes into the JSON report.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from collections import Counter

log = logging.getLogger(__name__)

BIN = "mathcat-cli"


def program() -> str | None:
    return os.environ.get("MATHCAT_BIN") or shutil.which(BIN)


# Readings already made in this process, by program and MathML: a document is tagged up to three times (#403)
_cache: dict[tuple[str, str], dict] = {}


def read_all(mathml: list[str], timeout: float = 120) -> list[dict]:
    """MathCAT's reading of each MathML, in order: {"speech", "nemeth", "ueb"}, or {"error"}. [] without MathCAT."""
    exe = program()
    if not exe or not mathml:
        return []
    todo = list(dict.fromkeys(m for m in mathml if (exe, m) not in _cache))
    if todo:
        got = _run(exe, todo, timeout)
        if not got:
            return []
        if len(_cache) > 5000:
            _cache.clear()
        _cache.update(zip(((exe, m) for m in todo), got))
    return [_cache[(exe, m)] for m in mathml]


def _run(exe: str, mathml: list[str], timeout: float) -> list[dict]:
    lines = "".join(json.dumps({"mathml": m}) + "\n" for m in mathml)
    try:
        done = subprocess.run([exe], input=lines, capture_output=True, text=True, timeout=timeout, check=True)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("MathCAT didn't run: %s", exc)
        return []
    answers = []
    for line in done.stdout.splitlines():
        try:
            answers.append(json.loads(line))
        except ValueError:
            answers.append({"error": "unreadable answer"})
    return answers if len(answers) == len(mathml) else []


# --- comparing two spoken readings ---------------------------------------------------------------------------

_ONES = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if i > 1}
# A power or a fraction's denominator said as a word: "x squared", "1 third", "the fifth power"
_ORDINAL = {"squared": 2, "cubed": 3, "half": 2, "halves": 2, "second": 2, "third": 3, "thirds": 3, "fourth": 4,
            "fourths": 4, "quarter": 4, "quarters": 4, "fifth": 5, "fifths": 5, "sixth": 6, "sixths": 6,
            "seventh": 7, "sevenths": 7, "eighth": 8, "eighths": 8, "ninth": 9, "ninths": 9, "tenth": 10,
            "tenths": 10}
GREEK = set("alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho sigma tau "
            "upsilon phi chi psi omega".split())
# Letters that are also English words: an AI's "a fraction" or "I" would count as a variable, so they don't count
_WORDS = {"a", "i"}


# An equation's number the AI says after the formula ("; equation 2.9", "(3.1b)"): a label, not part of the formula
# (an AI may put it in its LaTeX too, \tag{2.3} or (2.3) after the formula, and then it's in MathCAT's reading:
# "open paren 2.3 close paren")
_LABEL = re.compile(r"[\s;,.]*(?:\(?\s*(?:equation|eqn?\.?)\s*\(?[\d.]+\s?[a-z]?\)?|\(\s*[\d.]+\s?[a-z]?\s*\)"
                    r"|open paren\s*[\d.]+\s?[a-z]?\s*close paren)\s*[.;,]?\s*$", re.I)
# Two-letter words that look like a letter and its subscript ("Xc"): not split
_SHORT_WORDS = {"is", "it", "if", "in", "on", "or", "of", "as", "at", "an", "be", "by", "do", "go", "he", "me", "my",
                "no", "so", "to", "up", "us", "we", "am"}


def _letters_apart(text: str) -> str:
    """Letters written together that are said one by one: an acronym ("BIBO"), a letter with its subscript glued on
    ("Xc", "T1", "x2")."""
    def apart(m: re.Match) -> str:
        w = m.group(0)
        if w.lower() in _SHORT_WORDS:
            return w
        return " ".join(re.findall(r"[A-Za-z]|\d+(?:\.\d+)?", w))
    text = re.sub(r"\b([a-z]{2,})([A-Z])\b", r"\1 \2", text)  # a word run into a letter: "whereH"
    return re.sub(r"\b(?:[A-Z]{2,6}|[A-Z][a-z]|[A-Za-z]\d+)\b", apart, text)


def _words(text: str) -> list[str]:
    text = _letters_apart(text).lower().replace("-", " ")
    text = re.sub(r"\beigh\b", "a", text)  # MathCAT spells "a" so a speech engine says the letter
    text = re.sub(r"\bwith respect to\b", "d", text)  # "with respect to t" is "d t"
    tokens = re.findall(r"\d+(?:\.\d+)?|[a-z]+", text)
    out: list[str] = []
    for t in tokens:
        # "dx", "dy": the d and the variable, as MathCAT says them
        if re.fullmatch(r"d[a-z]", t) and t not in {"do"}:
            out += [t[0], t[1]]
        else:
            out.append(t)
    return out


def _numbers_and_letters(text: str) -> tuple[Counter, Counter]:
    words = _words(text)
    numbers: list[str] = []
    letters: list[str] = []
    value: int | None = None

    def close():
        nonlocal value
        if value is not None:
            numbers.append(str(value))
            value = None

    i = 0
    while i < len(words):
        w = words[i]
        if w in _ONES or w in _TENS:
            value = (value or 0) + _ONES.get(w, 0) + _TENS.get(w, 0)
        elif w in ("hundred", "thousand", "million") and value is not None:
            value *= {"hundred": 100, "thousand": 1000, "million": 1000000}[w]
        elif w == "point" and i + 1 < len(words) and (words[i + 1].isdigit() or words[i + 1] in _ONES):
            # "three point one four": the digits after the point, one by one
            whole = numbers.pop() if value is None and numbers else str(value or 0)
            value = None
            digits = ""
            while i + 1 < len(words) and (words[i + 1].isdigit() or words[i + 1] in _ONES):
                i += 1
                digits += words[i] if words[i].isdigit() else str(_ONES[words[i]])
            numbers.append(f"{whole}.{digits}")
        else:
            close()
            if re.fullmatch(r"\d+(?:\.\d+)?", w):
                numbers.append(str(float(w)).removesuffix(".0") if "." in w else str(int(w)))
            elif w in _ORDINAL:
                numbers.append(str(_ORDINAL[w]))
            elif (len(w) == 1 and w not in _WORDS) or w in GREEK:
                letters.append(w)
        i += 1
    close()
    return Counter(numbers), Counter(letters)


def compare(ai: str, rule: str) -> dict | None:
    """None when the two readings say the same numbers and letters; otherwise what each says that the other doesn't."""
    an, al = _numbers_and_letters(_LABEL.sub("", ai or ""))
    rn, rl = _numbers_and_letters(_LABEL.sub("", rule or ""))
    if an == rn and al == rl:
        return None
    return {"numbers": {"ai_only": sorted((an - rn).elements()), "rule_only": sorted((rn - an).elements())},
            "letters": {"ai_only": sorted((al - rl).elements()), "rule_only": sorted((rl - al).elements())}}


def speech_text(speech: str) -> str:
    """MathCAT's speech as text to read or show: its pauses (";") as commas, "eigh" as the letter it is."""
    text = re.sub(r"\s*;\s*", ", ", speech or "").strip(" ,")
    return re.sub(r"\beigh\b", "a", text)


# --- braille files -------------------------------------------------------------------------------------------

# North American Braille ASCII, by dot pattern (bit 0 is dot 1 ... bit 5 is dot 6): what a .brf file holds, and what
# embossers and braille displays read
_BRF = " A1B'K2L@CIF/MSP\"E3H9O6R^DJG>NTQ,*5<-U8V.%[$+X!&;:4\\0Z7(_?W]#Y)="
LINE_CELLS, PAGE_LINES = 40, 25


def to_brf(cells: str) -> str:
    """Unicode braille (U+2800...) as braille ASCII; a cell beyond six dots keeps its first six."""
    return "".join(_BRF[(ord(c) - 0x2800) & 0x3F] if 0x2800 <= ord(c) <= 0x28FF else " " for c in cells)


def _heading(text: str) -> str:
    """A short heading in uncontracted braille, as braille ASCII: letters as they are, numbers after the number sign."""
    out = []
    for word in text.lower().split():
        out.append("#" + "".join("JABCDEFGHI"[int(d)] for d in word) if word.isdigit() else word.upper())
    return " ".join(out)


def brf(formulas: list[dict], code: str, document: str) -> str | None:
    """The formulas in one braille code ("nemeth" or "ueb") as a .brf file: a heading line for each ("page 3 formula
    2"), its braille below, 40 cells to a line and 25 lines to a page. None when no formula has braille in that code."""
    lines = [_heading(f"formulas {code}"), ""]
    count = 0
    for f in formulas:
        cells = (f.get("rule") or {}).get(code)
        if not cells:
            continue
        count += 1
        lines.append(_heading(f"page {f['page'] + 1} formula {f['name'].rsplit('-', 1)[-1].split('.')[0]}"))
        text = to_brf(cells)
        lines += [text[i:i + LINE_CELLS] for i in range(0, len(text), LINE_CELLS)] or [""]
        lines.append("")
    if not count:
        return None
    pages = ["\r\n".join(lines[i:i + PAGE_LINES]) for i in range(0, len(lines), PAGE_LINES)]
    return "\f".join(pages) + "\r\n"
