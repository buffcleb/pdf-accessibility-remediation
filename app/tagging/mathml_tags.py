"""MathML a document's own tags carry (#403). LaTeX's tagging (tagpdf with luamml) and other PDF 2.0 producers put a
formula's MathML on its Formula tag in one or both of two ways:

- an associated file (/AF) on the Formula: an embedded MathML file, /AFRelationship /Supplement (and often the
  formula's LaTeX beside it, /Source);
- structure elements in the MathML namespace (math, mi, mo, mfrac...) under the Formula, each with /NS naming
  http://www.w3.org/1998/Math/MathML.

Kept tags keep both. MathML elements say what they are through their namespace, so they aren't role-mapped or
repaired like unknown roles of the default namespace, and the tag check accepts them.
"""
from __future__ import annotations

import pikepdf
from pikepdf import Dictionary

MATHML_NS = "http://www.w3.org/1998/Math/MathML"


def in_mathml(obj) -> bool:
    """A structure element in the MathML namespace."""
    ns = obj.get("/NS") if isinstance(obj, Dictionary) else None
    return isinstance(ns, Dictionary) and str(ns.get("/NS", "")) == MATHML_NS


def _files(obj) -> list:
    af = obj.get("/AF") if isinstance(obj, Dictionary) else None
    if isinstance(af, Dictionary):
        af = [af]
    return [f for f in (af or []) if isinstance(f, Dictionary)]


def _data(spec) -> bytes | None:
    try:
        stream = spec.get("/EF", {}).get("/UF") or spec.get("/EF", {}).get("/F")
        return stream.read_bytes() if isinstance(stream, pikepdf.Stream) else None
    except (pikepdf.PdfError, AttributeError, TypeError, ValueError):
        return None


def _subtype(spec) -> str:
    stream = (spec.get("/EF") or {}).get("/F") if isinstance(spec.get("/EF"), Dictionary) else None
    sub = stream.get("/Subtype") if isinstance(stream, pikepdf.Stream) else None
    return str(sub or spec.get("/Subtype") or "").lstrip("/").replace("#2F", "/").lower()


def formula_mathml(obj) -> tuple[str | None, str | None]:
    """A Formula's own MathML and LaTeX source, from its associated files: (MathML, LaTeX), either None."""
    mathml = latex = None
    for spec in _files(obj):
        name = str(spec.get("/UF") or spec.get("/F") or "").lower()
        kind = _subtype(spec)
        rel = str(spec.get("/AFRelationship") or "")
        if mathml is None and ("mathml" in kind or name.endswith((".mml", ".mathml"))):
            data = _data(spec)
            mathml = data.decode("utf-8", "replace") if data else None
        elif latex is None and (kind in ("application/x-tex", "text/x-tex") or (rel == "/Source" and
                                                                               name.endswith(".tex"))):
            data = _data(spec)
            latex = data.decode("utf-8", "replace") if data else None
    return mathml, latex


def has_mathml(obj, children=()) -> bool:
    """A Formula that carries MathML: as an associated file, or as MathML elements inside it."""
    return formula_mathml(obj)[0] is not None or any(in_mathml(getattr(c, "obj", c)) for c in children)
