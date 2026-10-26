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

from typing import Callable
from xml.sax.saxutils import escape, quoteattr

import pikepdf
from pikepdf import Array, Dictionary

from app.tagging.tree import kids as _kids

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


def _attrs(obj) -> str:
    """A MathML element's attributes, from its /A attribute dictionaries (their /O owner aside)."""
    a = obj.get("/A")
    out = []
    for d in ([a] if isinstance(a, Dictionary) else list(a) if isinstance(a, Array) else []):
        if not isinstance(d, Dictionary):
            continue
        for key, value in d.items():
            if key not in ("/O", "/NS"):
                out.append(f" {key[1:]}={quoteattr(str(value).lstrip('/'))}")
    return "".join(out)


def mathml_from_elements(formula, text_of: Callable[[object, int], str]) -> str | None:
    """A Formula's MathML rebuilt from the MathML structure elements under it (#421): names from their roles, nesting
    from the tree, attributes from /A, and each element's text from its marked content (text_of(page, mcid), the page
    from /Pg on the reference, the element or one above it) or its /ActualText. None when it has no MathML elements."""
    def build(obj, page) -> str:
        page = obj.get("/Pg", page) if isinstance(obj, Dictionary) else page
        name = str(obj.get("/S", "/mrow"))[1:]
        if "/ActualText" in obj:
            inner = escape(str(obj.ActualText))
        else:
            parts = []
            for k in _kids(obj):
                if isinstance(k, int):
                    parts.append(escape(text_of(page, k)))
                elif isinstance(k, Dictionary) and k.get("/Type") == "/MCR" and "/Stm" not in k:
                    parts.append(escape(text_of(k.get("/Pg", page), int(k.MCID))))
                elif isinstance(k, Dictionary) and in_mathml(k):
                    parts.append(build(k, page))
            inner = "".join(parts)
        return f"<{name}{_attrs(obj)}>{inner}</{name}>"

    page = formula.get("/Pg")
    top = [k for k in _kids(formula) if isinstance(k, Dictionary) and in_mathml(k)]
    if not top:
        return None
    if len(top) == 1 and str(top[0].get("/S")) == "/math":
        return build(top[0], page).replace("<math", f'<math xmlns="{MATHML_NS}"', 1)
    return f'<math xmlns="{MATHML_NS}">' + "".join(build(k, page) for k in top) + "</math>"
