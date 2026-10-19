"""Formulas as MathML (#403): the structured form of each formula the AI read, as music has MusicXML.

The AI's reading of a formula comes with LaTeX (app/pipeline/enrichment.py: notation in the text, and formula
figures). Each LaTeX is converted to MathML (latex2mathml, MIT) and checked: it must parse, use only MathML's
presentation elements, and give each fraction, root and script the parts it needs. LaTeX that fails (a brace the
model left open) gets no MathML, and the formula keeps its spoken reading as before. Text from the document is
escaped, and attributes that could point anywhere (href) or carry styling are dropped, so the files are inert.

What's made, when the document has formulas with MathML:
- a zip with one .mml file per formula (named by page and place), the formula list, and a note on what they are;
- the formula list on its own: an HTML page with every formula, by page, in MathML with its spoken reading.
  Browsers draw MathML, and screen readers (NVDA with MathCAT, JAWS, VoiceOver) let a person move through a
  formula piece by piece, which alt text can't do.
Each formula's MathML is also kept with its reading in the full JSON report ("math").
"""
from __future__ import annotations

import html
import logging
import re
import zipfile
import xml.etree.ElementTree as ET

log = logging.getLogger(__name__)

NS = "http://www.w3.org/1998/Math/MathML"

# MathML's presentation elements (MathML Core and the common rest). Anything else: no MathML for that formula.
ELEMENTS = {
    "math", "mrow", "mi", "mn", "mo", "mtext", "ms", "mspace", "mfrac", "msqrt", "mroot", "mstyle", "merror",
    "mpadded", "mphantom", "menclose", "msub", "msup", "msubsup", "munder", "mover", "munderover", "mmultiscripts",
    "mprescripts", "none", "mtable", "mtr", "mtd", "mlabeledtr", "mfenced", "semantics", "annotation",
}
# Attributes that shape the math; anything else (href, style, class, id, colors, event handlers) is dropped
ATTRIBUTES = {
    "display", "mathvariant", "stretchy", "fence", "form", "separator", "accent", "accentunder", "largeop",
    "movablelimits", "symmetric", "lspace", "rspace", "minsize", "maxsize", "linethickness", "width", "height",
    "depth", "columnalign", "rowalign", "columnspacing", "rowspacing", "columnlines", "rowlines", "frame",
    "notation", "mathsize", "displaystyle", "scriptlevel", "open", "close", "separators", "encoding", "voffset",
}
# How many parts each needs: a fraction with no denominator is LaTeX the model left unfinished
ARITY = {"mfrac": 2, "mroot": 2, "msub": 2, "msup": 2, "munder": 2, "mover": 2, "msubsup": 3, "munderover": 3}
_TEXT = re.compile(r"<(mtext|mi|mn|mo|ms)(\s[^<>]*)?>([^<]*(?:<(?!/\1>)[^<]*)*)</\1>")
_ENTITY = re.compile(r"&(?!#x[0-9A-Fa-f]+;|#[0-9]+;|lt;|gt;|amp;|quot;|apos;)")


def _escape_text(raw: str) -> str:
    """latex2mathml copies \\text{...} as it is: a "<" or "&" there would break the XML or smuggle in markup."""
    def fix(m: re.Match) -> str:
        inner = _ENTITY.sub("&amp;", m.group(3)).replace("<", "&lt;").replace(">", "&gt;")
        return f"<{m.group(1)}{m.group(2) or ''}>{inner}</{m.group(1)}>"
    return _TEXT.sub(fix, raw)


def _clean(el: ET.Element) -> bool:
    """Checks one element and its children, dropping attributes that aren't MathML's; False if it isn't MathML."""
    tag = el.tag.split("}", 1)[-1]
    if not el.tag.startswith("{" + NS + "}") or tag not in ELEMENTS:
        return False
    for name in list(el.attrib):
        if name not in ATTRIBUTES:
            del el.attrib[name]
    if tag in ARITY and len(el) != ARITY[tag]:
        return False
    return all(_clean(child) for child in el)


def _serialize(el: ET.Element) -> str:
    """The checked tree as text. Written out here rather than by ElementTree, whose namespace prefixes come from a
    registry the whole process shares (another module's default would turn <math> into <ns0:math>)."""
    tag = el.tag.split("}", 1)[-1]
    attrs = "".join(f' {k}="{html.escape(v, quote=True)}"' for k, v in el.attrib.items())
    if tag == "math":
        attrs = f' xmlns="{NS}"' + attrs
    inner = html.escape(el.text or "", quote=False) + "".join(
        _serialize(child) + html.escape(child.tail or "", quote=False) for child in el)
    return f"<{tag}{attrs}>{inner}</{tag}>" if inner else f"<{tag}{attrs}/>"


# An equation's number the AI copied into its LaTeX: \tag{2.3}, or a number in brackets set apart at the end
# ("\quad (2.27)"). It isn't part of the formula, and read as math it's a multiplication ("times 2.27"). A bracketed
# number written straight after a symbol, f(2.5), is part of the formula and stays.
_TAG = re.compile(r"\\tag\*?\{[^{}]*\}")
_NUMBER_LABEL = re.compile(r"(?:\\q?quad|\\[,;:!]|\\hfill|~)\s*\(\s*\d+(?:\.\d+)*\s?[a-z]?\s*\)\s*[.,]?\s*$")


def without_label(latex: str) -> str:
    return _NUMBER_LABEL.sub("", _TAG.sub("", latex)).strip()


def to_mathml(latex: str | None, display: bool = False) -> str | None:
    """The formula as MathML, with its LaTeX kept as an annotation; None when the LaTeX doesn't make good MathML."""
    latex = (latex or "").strip()
    if not latex or len(latex) > 4000:
        return None
    try:
        from latex2mathml.converter import convert
        raw = convert(without_label(latex) or latex, display="block" if display else "inline")
        root = ET.fromstring(_escape_text(raw))
    except Exception as exc:  # noqa: BLE001 - any LaTeX the converter or the parser can't take: no MathML
        log.debug("no MathML for %r: %s", latex[:80], exc)
        return None
    if root.tag != f"{{{NS}}}math" or not len(root) or not _clean(root):
        return None
    # <semantics>: the presentation, and the LaTeX it came from (MathML's own place for a source)
    semantics = ET.Element(f"{{{NS}}}semantics")
    if len(root) == 1 and root[0].tag == f"{{{NS}}}mrow":
        semantics.append(root[0])  # semantics takes one child: the converter's own row is that
    else:
        ET.SubElement(semantics, f"{{{NS}}}mrow").extend(list(root))
    annotation = ET.SubElement(semantics, f"{{{NS}}}annotation", {"encoding": "application/x-tex"})
    annotation.text = latex
    for child in list(root):
        root.remove(child)
    root.append(semantics)
    return _serialize(root)


def clean(mathml: str | bytes | None, latex: str | None = None) -> str | None:
    """MathML a document supplies (#403: LaTeX's tagging attaches it to each Formula tag), checked as ours is: it
    must parse and use only MathML's presentation elements; unknown attributes are dropped. Its LaTeX source, when
    the document has that too, is kept as the annotation. None when it isn't good MathML."""
    if not mathml or len(mathml) > 200_000:
        return None
    try:
        root = ET.fromstring(mathml)
    except ET.ParseError:
        return None
    if root.tag == "math":  # without its namespace: put it in MathML's
        for el in root.iter():
            if "}" not in el.tag:
                el.tag = f"{{{NS}}}{el.tag}"
    if root.tag != f"{{{NS}}}math" or not len(root) or not _clean(root):
        return None
    for el in root.iter():  # the layout's own spacing between elements, not content
        if len(el) and el.text and not el.text.strip():
            el.text = None
        if el.tail and not el.tail.strip():
            el.tail = None
    if root.find(f"{{{NS}}}semantics") is None and latex:
        semantics = ET.Element(f"{{{NS}}}semantics")
        if len(root) == 1 and root[0].tag == f"{{{NS}}}mrow":
            semantics.append(root[0])
        else:
            ET.SubElement(semantics, f"{{{NS}}}mrow").extend(list(root))
        for child in list(root):
            root.remove(child)
        ET.SubElement(semantics, f"{{{NS}}}annotation", {"encoding": "application/x-tex"}).text = latex.strip()
        root.append(semantics)
    return _serialize(root)


def collect(notation: list[dict], images=()) -> list[dict]:
    """Every formula the AI read with LaTeX, in page order, each with its MathML (None where it couldn't be made).
    notation: the readings in the text (their dicts get "mathml" too, for the JSON report's ai_notation);
    images: figures described as formulas (ImageRegion)."""
    found = []
    for n in notation:
        if not n.get("latex"):
            continue
        n["mathml"] = to_mathml(n["latex"], display=n.get("role") == "Formula")
        found.append({"page": n["page"], "bbox": n.get("bbox"), "source": "text", "spoken": n.get("spoken"),
                      "latex": n["latex"], "mathml": n["mathml"]})
    for img in images:
        if img.kind != "formula" or img.decorative or not img.latex:
            continue
        found.append({"page": img.page, "bbox": list(img.bbox), "source": "figure", "spoken": img.alt,
                      "latex": img.latex, "mathml": to_mathml(img.latex, display=True)})
    found.sort(key=lambda f: (f["page"], (f["bbox"] or [0, 0])[1], (f["bbox"] or [0])[0]))
    counts: dict[int, int] = {}
    for f in found:
        counts[f["page"]] = counts.get(f["page"], 0) + 1
        f["name"] = f"page-{f['page'] + 1:03d}_formula-{counts[f['page']]}.mml"
    return found


def _iou(a, b) -> float:
    inter = max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def add_document_formulas(formulas: list[dict], notation: list[dict], found: list[dict]) -> int:
    """The document's own formulas (MathML its tags carry), with the others (#403). The document's MathML is the
    formula as its author wrote it: an AI reading of the same place gives way to it, from the formulas and from the
    readings flagged for review. Returns how many were added."""
    def same(a, b) -> bool:
        return a["page"] == b["page"] and a.get("bbox") and b.get("bbox") and _iou(a["bbox"], b["bbox"]) >= 0.5
    counts: dict[int, int] = {}
    for f in sorted(found, key=lambda f: (f["page"], f["bbox"][1], f["bbox"][0])):
        counts[f["page"]] = counts.get(f["page"], 0) + 1
        f["name"] = f"page-{f['page'] + 1:03d}_tagged-{counts[f['page']]}.mml"
    formulas[:] = [f for f in formulas if not any(same(f, d) for d in found)] + found
    formulas.sort(key=lambda f: (f["page"], (f["bbox"] or [0, 0])[1], (f["bbox"] or [0])[0]))
    notation[:] = [n for n in notation if not any(same(n, d) for d in found)]
    return len(found)


def read_by_rule(formulas: list[dict]) -> int:
    """MathCAT's speech and braille for each formula with MathML ("rule": {"spoken", "nemeth", "ueb"}), and, where the
    AI's spoken reading says other numbers or letters, what differs ("disagreement"). Returns how many disagree.
    Without MathCAT (a run outside the engine image) the formulas are left as they are."""
    from app.pipeline import mathspeak
    todo = [f for f in formulas if f["mathml"]]
    answers = mathspeak.read_all([f["mathml"] for f in todo])
    disagree = 0
    for f, a in zip(todo, answers):
        if a.get("error") or not a.get("speech"):
            continue
        f["rule"] = {"spoken": mathspeak.speech_text(a["speech"]), "nemeth": a.get("nemeth"), "ueb": a.get("ueb")}
        diff = mathspeak.compare(f["spoken"], f["rule"]["spoken"]) if f.get("spoken") else None
        if diff:
            f["disagreement"] = diff
            disagree += 1
    return disagree


# What a picture reader writes around a formula that isn't part of it: an equation's number at the end, "\left(2.17
# \right)" or "(2.62\mathrm{b})", and runs of spacing it pads with
_SPACE = r"(?:\\q?quad|\\[ ,;:!]|~|\\mathrm\{\s*~*\s*\\*\s*\}|\\\\)"
_TRAILING_LABEL = re.compile(_SPACE + r"+\s*(?:\\left)?\(\s*[\d.\s]+\s*(?:\\mathrm\{[a-z]\}|[a-z])?\s*(?:\\right)?\)"
                             r"\s*[.,]?\s*$")
_TRAILING_SPACE = re.compile(r"(?:" + _SPACE + r"|\s|\.|\\)+$")
# What it writes when it can't read the picture (a diagram, a crop with text in it): no formula has these
_GARBLED = re.compile(r"\\begin\{array\}|\\nonumber|\\phantom|\\underline\{\{\{|(?:" + _SPACE + r"\s*){6,}")


def picture_latex(latex: str) -> str | None:
    """A picture reader's LaTeX made comparable: its equation number and padding taken off; None when it shows the
    signs of a picture it couldn't read."""
    latex = _TRAILING_SPACE.sub("", _TRAILING_LABEL.sub("", (latex or "").strip()))
    if not latex or _GARBLED.search(latex):
        return None
    return latex


def _comparable(a: str, b: str) -> bool:
    """Two readings worth comparing: about as much in each (a reading cut short, or one that took in its neighbours,
    says nothing about the other)."""
    from app.pipeline.mathspeak import _numbers_and_letters
    na = sum(sum(c.values()) for c in _numbers_and_letters(a))
    nb = sum(sum(c.values()) for c in _numbers_and_letters(b))
    return min(na, nb) >= max(na, nb) * 0.6


def read_from_pictures(formulas: list[dict], pdf_path) -> int:
    """A second, independent reading of each formula the AI read: from its picture, by the formula reading service
    (app/pipeline/formula_ocr.py). Its LaTeX becomes MathML and is read by rule like the AI's, so the two readings
    are compared in the same words; where they say other numbers or letters, the formula is flagged with both
    A picture reader is noisier than the AI: its equation numbers and padding are taken off, and a reading that shows
    it couldn't read the picture (a diagram, a crop with text in it), or that says far more or less than the AI's, is
    set aside rather than flagged
    ("picture": {"latex", "mathml", "spoken"}, "picture_disagreement"). Returns how many disagree. Without the
    service, or without MathCAT to compare with, the formulas are left as they are."""
    from app.config import get_settings
    from app.pipeline import formula_ocr, mathspeak
    todo = [f for f in formulas if f.get("mathml") and f.get("rule") and f.get("source") in ("text", "figure")]
    todo = todo[:get_settings().formula_ocr_max]
    if not todo:
        return 0
    pairs = [(f, png) for f, png in zip(todo, formula_ocr.pictures(pdf_path, todo)) if png]
    answers = formula_ocr.read_all([png for _, png in pairs])
    read = [(f, m, latex) for (f, _), a in zip(pairs, answers)
            if a.get("ok") and (latex := picture_latex(a["latex"])) and (m := to_mathml(latex, display=True))]
    speech = mathspeak.read_all([m for _, m, _ in read])
    disagree = 0
    for (f, m, latex), said in zip(read, speech):
        if said.get("error") or not said.get("speech"):
            continue
        spoken = mathspeak.speech_text(said["speech"])
        if not _comparable(f["rule"]["spoken"], spoken):
            continue  # one of them is cut short or took in its neighbours: nothing to learn from comparing
        f["picture"] = {"latex": latex, "mathml": m, "spoken": spoken}
        diff = mathspeak.compare(f["rule"]["spoken"], f["picture"]["spoken"])
        if diff:
            f["picture_disagreement"] = diff
            disagree += 1
    return disagree


def flagged(f: dict) -> bool:
    """A formula whose readings disagree: the AI's spoken reading and its LaTeX read by rule, or its LaTeX and its
    picture."""
    return bool(f.get("disagreement") or f.get("picture_disagreement"))


def _readings(f: dict) -> list[dict]:
    out = [{"source": "ai", "label": "The AI's reading", "text": f["spoken"]}]
    if f.get("rule"):
        out.append({"source": "rule", "label": "Read by rule (MathCAT) from the AI's transcription",
                    "text": f["rule"]["spoken"]})
    if f.get("picture"):
        out.append({"source": "picture", "label": "Read from the formula's picture (the formula reader)",
                    "text": f["picture"]["spoken"]})
    return out


def disagreements(formulas: list[dict]) -> list[dict]:
    """The formulas whose readings disagree, for the JSON report: every reading, what differs, which one the PDF has
    (the AI's, which was checked against the page's own text) and which a person chose (none yet; an edit that
    picks one records it). "differs" is the AI's spoken reading against its LaTeX read by rule; "differs_picture",
    that against the formula read from its picture."""
    return [{"formula": f["name"], "page": f["page"] + 1, "bbox": f["bbox"], "source": f["source"],
             "latex": f["latex"], "mathml": f["mathml"], "differs": f.get("disagreement"),
             "differs_picture": f.get("picture_disagreement"),
             **({"picture_latex": f["picture"]["latex"]} if f.get("picture") else {}),
             "readings": {r["source"]: r["text"] for r in _readings(f)}, "in_pdf": "ai", "chosen": None}
            for f in formulas if flagged(f)]


def finding_extra(f: dict) -> dict:
    """What a review finding carries so Edit tag can show the readings and the formula, and take a choice."""
    return {"formula": f["name"], "latex": f["latex"], "mathml": f["mathml"], "differs": f.get("disagreement"),
            "differs_picture": f.get("picture_disagreement"), "readings": _readings(f),
            **({"picture_mathml": f["picture"]["mathml"]} if f.get("picture_disagreement") else {})}


def formula_list(formulas: list[dict], document: str) -> str:
    """The formulas on one HTML page, by page: MathML for reading (and drawing), the spoken reading beside it, and
    where reading it by rule says something else, that reading too, marked to be checked."""
    title = f"Formulas in {document}"
    rows = []
    page = None
    for f in formulas:
        if not f["mathml"]:
            continue
        if f["page"] != page:
            if page is not None:
                rows.append("</ol>")
            page = f["page"]
            rows.append(f"<h2>Page {page + 1}</h2>\n<ol>")
        spoken = html.escape(f.get("spoken") or "")
        row = f"<li><div class=\"math\">{f['mathml']}</div>"
        if spoken:
            row += f"<p class=\"spoken\">Read aloud as: {spoken}</p>"
        if f.get("disagreement"):
            row += (f"<p class=\"differs\"><strong>Check this one:</strong> read by rule from the formula it's "
                    f"\u201c{html.escape(f['rule']['spoken'])}\u201d, which says other numbers or letters.</p>")
        if f.get("picture_disagreement"):
            row += (f"<p class=\"differs\"><strong>Check this one:</strong> read from its picture it's "
                    f"\u201c{html.escape(f['picture']['spoken'])}\u201d, which says other numbers or letters.</p>")
        rows.append(row + "</li>")
    if page is not None:
        rows.append("</ol>")
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        # Nothing on this page runs or loads anything: MathML is drawn by the browser itself
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'\">\n"
        f"<title>{html.escape(title)}</title>\n"
        "<style>body{font-family:system-ui,sans-serif;max-width:50rem;margin:2rem auto;padding:0 1rem;line-height:1.5}"
        "li{margin:0 0 1.25rem}.math{font-size:1.25rem;overflow-x:auto}"
        ".math math{display:inline math;math-style:normal}"
        ".spoken{color:#475467;margin:.25rem 0 0}.differs{color:#8a3a00;margin:.25rem 0 0}"
        "</style>\n</head>\n<body>\n"
        f"<h1>{html.escape(title)}</h1>\n"
        "<p>Each formula the document's AI reading found, by page, as MathML: a screen reader can read it and let you "
        "move through it part by part, and the browser draws it. Below each is how it's read aloud in the "
        "accessible PDF. They were read by AI, not by a person: check them against the page before relying on "
        "them.</p>\n"
        + "\n".join(rows) + "\n</body>\n</html>\n")


def write_archive(path, formulas: list[dict], document: str, braille: dict[str, str]) -> None:
    """One .mml file per formula, the formula list, the braille files, and a note on what they are."""
    usable = [f for f in formulas if f["mathml"]]
    note = (f"Formulas read from {document}\n\n"
            "formulas.html: every formula on one page, by page. Open it in a web browser: it draws them, and a "
            "screen reader can read each one and move through it part by part. Start here.\n\n"
            "Each page-… .mml file is one formula as MathML, named by its page and its place on the page (top to "
            "bottom). Math software, equation editors and braille translators take MathML; each file also holds "
            "the LaTeX it was made from.\n\n"
            + ("formulas-nemeth.brf and formulas-ueb.brf: every formula in braille, in Nemeth and in UEB math, for "
               "a braille display or an embosser (40 cells to a line, 25 lines to a page), each under a heading "
               "with its page and number. The braille is made by MathCAT from the MathML.\n\n" if braille else "")
            + "The formulas were read by AI, not by a person. They are usually right but can have a wrong symbol, "
            "exponent or sign: check them against the page before relying on them.\n")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", note)
        z.writestr("formulas.html", formula_list(formulas, document))
        for code, data in braille.items():
            z.writestr(f"formulas-{code}.brf", data)
        for f in usable:
            z.writestr(f["name"], "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n" + f["mathml"] + "\n")


def outputs(out, stem: str, notation: list[dict], images=(), formulas: list[dict] | None = None,
            ) -> tuple[list[dict], dict[str, str]]:
    """Writes the downloads when any formula has MathML: (the formulas, for the JSON report; the files by kind:
    math_files, math_list, and math_nemeth and math_ueb when MathCAT read them). `formulas` as collected (and read by
    rule) earlier in the job, or collected here from `notation` and `images`. Only documents with formulas get the
    files, as only those with music get its."""
    from app.pipeline import mathspeak
    formulas = collect(notation, images) if formulas is None else formulas
    if not any(f["mathml"] for f in formulas):
        return formulas, {}
    document = f"{stem}.pdf"
    braille = {code: data for code in ("nemeth", "ueb")
               if (data := mathspeak.brf(formulas, code, document)) is not None}
    files = {"math_files": f"{stem}_math.zip", "math_list": f"{stem}_formulas.html"}
    write_archive(out / files["math_files"], formulas, document, braille)
    (out / files["math_list"]).write_text(formula_list(formulas, document), encoding="utf-8")
    for code, data in braille.items():
        files[f"math_{code}"] = f"{stem}_math_{code}.brf"
        (out / files[f"math_{code}"]).write_bytes(data.encode("ascii"))
    return formulas, files
