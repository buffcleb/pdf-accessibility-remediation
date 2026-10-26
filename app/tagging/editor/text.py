"""Text properties: alt and actual text, a link's description, a form field's label, a tag's language, and the
document's own title and language."""
from __future__ import annotations

import re

from pikepdf import Dictionary, String

from app.tagging.editor._ctx import MAX_TEXT, EditError, register
from app.tagging.metadata import set_language, set_title
from app.tagging.tree import items as _items
from app.verification.integrity import Elem

MAX_TITLE = 500
_LANG = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*$")


def _set_field_label(e: Elem, label: str) -> str:
    """A Form tag's field gets the label as its tooltip (/TU), what assistive technology announces (#341). A widget
    with a field of its own (or a radio button that already has its own tooltip) keeps it on itself; otherwise the
    field the widget belongs to."""
    if e.role != "Form":
        raise EditError(f"element {e.index} is a {e.role}: only a form field's tag has a field label")
    widget = next((it.Obj for it in _items(e.obj.get("/K")) if isinstance(it, Dictionary) and "/Obj" in it
                   and isinstance(it.Obj, Dictionary)), None)
    if widget is None:
        raise EditError(f"element {e.index} holds no form field")
    parent = widget.get("/Parent")
    target = widget if ("/T" in widget or "/TU" in widget or not isinstance(parent, Dictionary)) else parent
    target.TU = String(label.strip())
    return f"element {e.index} (Form): field label set"


def _set_link_text(e: Elem, text: str, n: int) -> str:
    """A link's description (#364): the tag's /Alt and each of its annotations' /Contents, which is what assistive
    technology announces for the link (PDF/UA wants it on every link annotation, so it can't be removed)."""
    text = text.strip()
    if not text:
        raise EditError(f"Edit {n}: a link needs a description; change it rather than removing it.")
    annots = [it.Obj for it in _items(e.obj.get("/K")) if isinstance(it, Dictionary) and "/Obj" in it
              and isinstance(it.Obj, Dictionary)]
    e.obj.Alt = String(text)
    for a in annots:
        a.Contents = String(text)
    return f"element {e.index} ({e.role}): description set" + (f" on {len(annots)} annotation(s)" if annots else "")


def _set_text(obj: Dictionary, key: str, text: str, e: Elem, label: str) -> str:
    text = text.strip()
    if text:
        obj[key] = String(text)
        return f"element {e.index} ({e.role}): {label} set"
    if key in obj:
        del obj[key]
    return f"element {e.index} ({e.role}): {label} removed"


def _check_doc_title(op: dict, n: int) -> None:
    title = op.get("title")
    if not isinstance(title, str) or not title.strip() or len(title) > MAX_TITLE:
        raise EditError(f"Edit {n}: a title is between 1 and {MAX_TITLE} characters.")


def _check_doc_lang(op: dict, n: int) -> None:
    if not _LANG.match(str(op.get("lang") or "")):
        raise EditError(f"Edit {n}: {op.get('lang')!r} isn't a language code like en-US. "
                        "A document must have one.")


def _check_field_label(op: dict, n: int) -> None:
    label = op.get("label")
    if not isinstance(label, str) or not label.strip() or len(label) > 300:
        raise EditError(f"Edit {n}: a field label is between 1 and 300 characters.")


def _check_text(op: dict, n: int) -> None:
    text = op.get("alt" if op["op"] == "set_alt" else "text")
    if not isinstance(text, str) or len(text) > MAX_TEXT:
        raise EditError(f"Edit {n}: text must be at most {MAX_TEXT} characters.")


def _check_lang(op: dict, n: int) -> None:
    if op.get("lang") and not _LANG.match(str(op["lang"])):
        raise EditError(f"Edit {n}: {op.get('lang')!r} isn't a language code like en-US.")


def _doc_title(ctx, op, e, t, n) -> str:
    set_title(ctx.pdf, op["title"].strip())
    return f"document title set to \"{op['title'].strip()}\""


def _doc_lang(ctx, op, e, t, n) -> str:
    set_language(ctx.pdf, op["lang"])
    return f"document language set to {op['lang']}"


def _alt(ctx, op, e, t, n) -> str:
    if e.role in ("Link", "Annot"):
        return _set_link_text(e, op["alt"], n)
    return _set_text(e.obj, "/Alt", op["alt"], e, "alt text")


def _lang(ctx, op, e, t, n) -> str:
    obj = e.obj
    lang = (op.get("lang") or "").strip()
    if lang:
        obj.Lang = String(lang)
    elif "/Lang" in obj:
        del obj["/Lang"]
    return f"element {e.index}: language {'set to ' + lang if lang else 'removed'}"


register("set_doc_title", _doc_title, _check_doc_title, element=False)
register("set_doc_lang", _doc_lang, _check_doc_lang, element=False)
register("set_alt", _alt, _check_text)
register("set_actual_text", lambda ctx, op, e, t, n: _set_text(e.obj, "/ActualText", op["text"], e, "actual text"),
         _check_text)
register("set_field_label", lambda ctx, op, e, t, n: _set_field_label(e, op["label"]), _check_field_label)
register("set_lang", _lang, _check_lang)
