"""What a screen reader calls each form field (#341).

A field's tooltip (/TU) is what assistive technology announces. Without one, the engine used to copy the internal
name ("Text1", "Check Box3"), which tells a person nothing. Here each field without a tooltip, or with one that is
only an internal name, is named from the page:

- a text field or list by its printed label: the words just left of it on its row, else the line just above it;
- a checkbox or radio button by the words just right of it (its option), with its question (the words left of or
  above the group) in front: "Payment method: Card". A radio group's own tooltip is the question;
- what the page doesn't label, the document's AI level is asked, from a picture of the field and what's around it.

A tooltip that already reads like words is never touched. Labels are keyed by the field's full name, which survives
the file being rewritten between here and tagging.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import pymupdf as fitz

log = logging.getLogger(__name__)

# Names a form tool gives a field when nobody names it, and machine names (XFA paths, underscores and numbers)
_INTERNAL = re.compile(
    r"^(?:(?:text|textfield|check ?box|checkbox|radio ?button|radio|group|button|push ?button|drop ?down|dropdown|"
    r"list ?box|combo ?box|choice|field|fill|untitled|undefined|form ?field|txt|cb|chk|rb)"
    r"[\s_.\-#]*\d*[a-z]?|[\w.\[\]]*\[\d+\][\w.\[\]]*|[a-z]*[_\d][\w]*\d[\w]*)$", re.I)
_CLEAN = re.compile(r"^[\s:*.\-_…·]+|[\s:*._…·()\[\]\-]+$")
MAX_LABEL = 80
ROW_GAP, ABOVE_GAP, WORD_GAP = 220.0, 24.0, 18.0


def looks_internal(name: str | None) -> bool:
    """A missing tooltip, or one that's a form tool's default or a machine name rather than words."""
    text = (name or "").strip()
    if not text or not any(ch.isalpha() for ch in text):
        return True
    return bool(_INTERNAL.match(text))


@dataclass
class Field:
    name: str  # the full name, parent.child
    kind: str  # text, checkbox, radio, choice, button, signature
    page: int
    rect: tuple[float, float, float, float]  # PyMuPDF space (top-left origin)
    tooltip: str
    option: str = ""  # a checkbox's or radio button's label (right of it)
    pdf_rect: tuple = ()  # the widget's /Rect as the file has it, to find the same widget again when applying


@dataclass
class Labels:
    fields: dict[str, str] = field(default_factory=dict)  # full name -> the field's tooltip
    widgets: dict[tuple, str] = field(default_factory=dict)  # a radio button's /Rect (rounded) -> "Q: option"
    from_page: int = 0
    from_ai: int = 0
    unnamed: list[str] = field(default_factory=list)  # still nothing to call them


def _kind(w) -> str:
    t = w.field_type
    return {fitz.PDF_WIDGET_TYPE_TEXT: "text", fitz.PDF_WIDGET_TYPE_CHECKBOX: "checkbox",
            fitz.PDF_WIDGET_TYPE_RADIOBUTTON: "radio", fitz.PDF_WIDGET_TYPE_COMBOBOX: "choice",
            fitz.PDF_WIDGET_TYPE_LISTBOX: "choice", fitz.PDF_WIDGET_TYPE_SIGNATURE: "signature",
            fitz.PDF_WIDGET_TYPE_BUTTON: "button"}.get(t, "text")


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", _CLEAN.sub("", text or "")).strip()
    text = re.sub(r"_{2,}.*$", "", text).strip()  # "Name ________" -> "Name"
    text = _CLEAN.sub("", text).strip()
    return text[:MAX_LABEL] if any(ch.isalpha() for ch in text) else ""


def _v_overlap(a, b) -> float:
    return max(0.0, min(a[3], b[3]) - max(a[1], b[1])) / max(1e-6, min(a[3] - a[1], b[3] - b[1]))


def _h_overlap(a, b) -> float:
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0]))


def _inside(word, rects) -> bool:
    cx, cy = (word[0] + word[2]) / 2, (word[1] + word[3]) / 2
    return any(r[0] - 1 <= cx <= r[2] + 1 and r[1] - 1 <= cy <= r[3] + 1 for r in rects)


def _run(words: list, start: int, step: int) -> str:
    """Words in a row from `start` outwards (step -1: leftwards), until a wide gap."""
    picked = [words[start]]
    i = start + step
    while 0 <= i < len(words):
        prev, cur = picked[-1], words[i]
        gap = (prev[0] - cur[2]) if step < 0 else (cur[0] - prev[2])
        if gap > WORD_GAP:
            break
        picked.append(cur)
        i += step
    picked.sort(key=lambda w: w[0])
    return " ".join(w[4] for w in picked)


def _left(rect, words) -> str:
    row = sorted((w for w in words if _v_overlap(w, rect) >= 0.5 and w[2] <= rect[0] + 2 and rect[0] - w[2] <= ROW_GAP),
                 key=lambda w: w[0])
    return _clean(_run(row, len(row) - 1, -1)) if row else ""


def _right(rect, words) -> str:
    row = sorted((w for w in words if _v_overlap(w, rect) >= 0.5 and w[0] >= rect[2] - 2 and w[0] - rect[2] <= ROW_GAP),
                 key=lambda w: w[0])
    return _clean(_run(row, 0, 1)) if row and row[0][0] - rect[2] <= WORD_GAP * 2 else ""


def _above(rect, words) -> str:
    near = [w for w in words if w[3] <= rect[1] + 2 and rect[1] - w[3] <= ABOVE_GAP
            and (_h_overlap(w, rect) > 0 or abs(w[0] - rect[0]) <= 15)]
    if not near:
        return ""
    bottom = max(w[3] for w in near)
    line = sorted((w for w in near if bottom - w[3] <= 3), key=lambda w: w[0])
    return _clean(_run(line, 0, 1))


def collect(path) -> list[Field]:
    out: list[Field] = []
    with fitz.open(path) as doc:
        for page in doc:
            for w in page.widgets() or []:
                out.append(Field(w.field_name or "", _kind(w), page.number, tuple(w.rect), w.field_label or "",
                                 pdf_rect=_raw_rect(doc, w.xref)))
    return out


def find(path, fields: list[Field] | None = None) -> tuple[Labels, list[Field]]:
    """Labels from the page. Returns them and the fields still without one (for the AI)."""
    fields = collect(path) if fields is None else fields
    labels, missing = Labels(), []
    if not fields:
        return labels, missing
    with fitz.open(path) as doc:
        words_by_page: dict[int, list] = {}
        rects_by_page: dict[int, list] = {}
        for f in fields:
            rects_by_page.setdefault(f.page, []).append(f.rect)
        for p in rects_by_page:
            words_by_page[p] = [w for w in doc[p].get_text("words") if not _inside(w, rects_by_page[p])]
        groups: dict[str, list[Field]] = {}
        for f in fields:
            groups.setdefault(f.name, []).append(f)
        for name, members in groups.items():
            first = members[0]
            if first.kind in ("button", "signature") or not looks_internal(first.tooltip):
                continue
            words = words_by_page[first.page]
            if first.kind in ("checkbox", "radio"):
                for m in members:
                    m.option = _right(m.rect, words_by_page[m.page]) or _left(m.rect, words_by_page[m.page])
                top = min(members, key=lambda m: (m.rect[1], m.rect[0]))
                union = (min(m.rect[0] for m in members), min(m.rect[1] for m in members),
                         max(m.rect[2] for m in members), max(m.rect[3] for m in members))
                question = _above(union, words)
                if len(members) == 1 and not question:
                    left = _left(top.rect, words)
                    question = left if left and left != top.option else ""
                if first.kind == "radio" and len(members) > 1:
                    options = [m.option for m in members if m.option]
                    if not (question or options):
                        missing.append(first)
                        continue
                    labels.fields[name] = question or ", ".join(options)
                    for m in members:
                        if m.pdf_rect:
                            labels.widgets[m.pdf_rect] = (f"{question}: {m.option}" if question and m.option
                                                          else (m.option or question))
                else:
                    text = f"{question}: {first.option}" if question and first.option else (first.option or question)
                    if not text:
                        missing.append(first)
                        continue
                    labels.fields[name] = text
                labels.from_page += 1
            else:
                text = _left(first.rect, words) or _above(first.rect, words)
                if not text:
                    missing.append(first)
                    continue
                labels.fields[name] = text
                labels.from_page += 1
    return labels, missing


def crop(path, f: Field, dpi: int = 110) -> bytes:
    """The field outlined in red, with the space a label usually takes around it."""
    with fitz.open(path) as doc:
        page = doc[f.page]
        r = fitz.Rect(f.rect)
        clip = fitz.Rect(r.x0 - 260, r.y0 - 70, r.x1 + 160, r.y1 + 30) & page.rect
        shape = page.new_shape()
        shape.draw_rect(r)
        shape.finish(color=(1, 0, 0), width=2)
        shape.commit()
        return page.get_pixmap(dpi=dpi, clip=clip).tobytes("png")


def nearby_text(path, f: Field) -> str:
    with fitz.open(path) as doc:
        page = doc[f.page]
        r = fitz.Rect(f.rect)
        return page.get_text("text", clip=fitz.Rect(r.x0 - 260, r.y0 - 70, r.x1 + 160, r.y1 + 30)).strip()[:600]


async def ask_ai(ai, path, missing: list[Field], labels: Labels, limit: int, stats=None) -> None:
    """The AI names what the page doesn't. Every field it can't is left for a person."""
    seen: set[str] = set()
    todo = [f for f in missing if not (f.name in seen or seen.add(f.name))]
    if stats is not None and len(todo) > limit:
        stats.limit_hit("AI_FIELD_LABELS_MAX", limit, len(todo), "form fields weren't named by the AI")
    for f in todo[:limit]:
        try:
            text = _clean(await ai.name_field(crop(path, f), f.page, f.kind, nearby_text(path, f)))
        except Exception as exc:  # noqa: BLE001 - an AI failure leaves the field for a person, never fails the job
            log.info("no AI label for field %s: %s", f.name, exc)
            text = ""
        if text:
            labels.fields[f.name] = text
            labels.from_ai += 1
        else:
            labels.unnamed.append(f.name)
    labels.unnamed += [f.name for f in todo[limit:]]


def apply(pdf, labels: Labels) -> int:
    """Set the tooltips on the PDF (pikepdf), by full field name. Returns how many fields were named."""
    import pikepdf

    form = pdf.Root.get("/AcroForm")
    if not isinstance(form, pikepdf.Dictionary) or not (labels.fields or labels.widgets):
        return 0
    named = 0
    seen: set = set()
    stack = [(f, "") for f in (form.get("/Fields") or [])]
    while stack:
        node, prefix = stack.pop()
        if not isinstance(node, pikepdf.Dictionary):
            continue
        if node.is_indirect:
            if node.objgen in seen:
                continue
            seen.add(node.objgen)
        part = str(node.get("/T", "")) if "/T" in node else ""
        full = f"{prefix}.{part}" if prefix and part else (part or prefix)
        if part and full in labels.fields and looks_internal(str(node.get("/TU", "") or "")):
            node.TU = pikepdf.String(labels.fields[full])
            named += 1
        # A radio button, as a group's child or as a field of its own: "question: option", by where it is
        if labels.widgets and (text := labels.widgets.get(_rounded(node.get("/Rect")))):
            node.TU = pikepdf.String(text)
        for kid in node.get("/Kids") or []:
            stack.append((kid, full))
    return named


def _rounded(rect) -> tuple:
    try:
        return tuple(round(float(v), 1) for v in rect) if rect is not None and len(rect) == 4 else ()
    except (TypeError, ValueError):
        return ()


def _raw_rect(doc, xref: int) -> tuple:
    """A widget's /Rect as written in the file (PDF space), rounded."""
    try:
        kind, value = doc.xref_get_key(xref, "Rect")
        return _rounded(value.strip("[]").split()) if kind == "array" else ()
    except Exception:  # noqa: BLE001
        return ()
