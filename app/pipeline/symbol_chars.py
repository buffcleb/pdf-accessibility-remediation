"""Characters set in symbol fonts, as Unicode (#605). Word writes a Symbol or Wingdings character as U+F000 plus its
code in the font (a Symbol bullet is U+F0B7, a Wingdings arrow U+F0E0), and Adobe's fonts put the pieces of tall
brackets at U+F8E5-U+F8FE: private use code points a screen reader has nothing to say for. Which character such a
code point stands for depends on the font, so it is mapped only where the font is known: Symbol through Adobe's Symbol
encoding, Wingdings through the codes Word uses for bullets, arrows and ticks. Codes with no good Unicode equivalent
are left as they are rather than guessed."""
from __future__ import annotations

import re

# Adobe's Symbol encoding: code -> Unicode (the Greek letters, operators, arrows, and the pieces of tall brackets)
_SYMBOL = {
    0x22: "∀", 0x24: "∃", 0x27: "∋", 0x2A: "∗", 0x2D: "−", 0x40: "≅",
    **dict(zip(range(0x41, 0x5B), "ΑΒΧΔΕΦΓΗΙϑΚΛΜΝΟΠΘΡΣΤΥςΩΞΨΖ")),
    0x5C: "∴", 0x5E: "⊥",
    **dict(zip(range(0x61, 0x7B), "αβχδεφγηιϕκλμνοπθρστυϖωξψζ")),
    0x7E: "∼",
    0xA0: "€", 0xA1: "ϒ", 0xA2: "′", 0xA3: "≤", 0xA4: "⁄", 0xA5: "∞", 0xA6: "ƒ", 0xA7: "♣", 0xA8: "♦", 0xA9: "♥",
    0xAA: "♠", 0xAB: "↔", 0xAC: "←", 0xAD: "↑", 0xAE: "→", 0xAF: "↓", 0xB0: "°", 0xB1: "±", 0xB2: "″", 0xB3: "≥",
    0xB4: "×", 0xB5: "∝", 0xB6: "∂", 0xB7: "•", 0xB8: "÷", 0xB9: "≠", 0xBA: "≡", 0xBB: "≈", 0xBC: "…", 0xBF: "↵",
    0xC0: "ℵ", 0xC1: "ℑ", 0xC2: "ℜ", 0xC3: "℘", 0xC4: "⊗", 0xC5: "⊕", 0xC6: "∅", 0xC7: "∩", 0xC8: "∪", 0xC9: "⊃",
    0xCA: "⊇", 0xCB: "⊄", 0xCC: "⊂", 0xCD: "⊆", 0xCE: "∈", 0xCF: "∉", 0xD0: "∠", 0xD1: "∇", 0xD2: "®", 0xD3: "©",
    0xD4: "™", 0xD5: "∏", 0xD6: "√", 0xD7: "⋅", 0xD8: "¬", 0xD9: "∧", 0xDA: "∨", 0xDB: "⇔", 0xDC: "⇐", 0xDD: "⇑",
    0xDE: "⇒", 0xDF: "⇓", 0xE0: "◊", 0xE1: "〈", 0xE2: "®", 0xE3: "©", 0xE4: "™", 0xE5: "∑",
    **dict(zip(range(0xE6, 0xF0), "⎛⎜⎝⎡⎢⎣⎧⎨⎩⎪")),
    0xF1: "〉", 0xF2: "∫", 0xF3: "⌠", 0xF4: "⎮", 0xF5: "⌡",
    **dict(zip(range(0xF6, 0xFF), "⎞⎟⎠⎤⎥⎦⎫⎬⎭")),
}
# Wingdings: only the codes Word uses for bullets, arrows and ticks (its other pictures have no plain equivalent)
_WINGDINGS = {
    0x6C: "●", 0x6E: "■", 0x71: "❑", 0x76: "❖", 0xA7: "▪", 0xD8: "➢", 0xDF: "←", 0xE0: "→", 0xE1: "↑", 0xE2: "↓",
    0xE8: "➔", 0xFB: "✗", 0xFC: "✓",
}
# Adobe's private use code points for the pieces of tall brackets, braces and the integral (in any font)
_ADOBE_PIECES = dict(zip(range(0xF8EB, 0xF8FF), "⎛⎜⎝⎡⎢⎣⎧⎨⎩⎪⎮⎞⎟⎠⎤⎥⎦⎫⎬⎭"))

_STYLE = re.compile(r"[-,].*$")


def family(font: str) -> str | None:
    """"symbol" or "wingdings" for those fonts (a subset prefix, style and MT suffix aside), else None. Wingdings 2 and
    3, and Unicode fonts with "Symbol" in their name (Segoe UI Symbol), are other tables, so not these."""
    name = _STYLE.sub("", font.split("+")[-1].strip("/")).lower()
    return "symbol" if name in ("symbol", "symbolmt") else "wingdings" if name == "wingdings" else None


def unicode_for(font: str, ch: str) -> str:
    """ch as Unicode, where it is a private use code point this font's table gives the meaning of; else ch."""
    if len(ch) != 1 or not "" <= ch <= "":
        return ch
    o = ord(ch)
    if o in _ADOBE_PIECES:
        return _ADOBE_PIECES[o]
    if 0xF020 <= o <= 0xF0FF:
        code, fam = o - 0xF000, family(font)
        if fam == "symbol":
            # the codes Symbol doesn't give a symbol of its own are ASCII's: digits, brackets, "=", "+"
            return _SYMBOL.get(code) or (chr(code) if code < 0x7F else ch)
        if fam == "wingdings":
            return _WINGDINGS.get(code, ch)
    return ch
