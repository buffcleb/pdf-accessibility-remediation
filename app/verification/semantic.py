"""Layer 2: does each tag fit the content it covers?

Page content is read through pdfplumber (pdfminer), a parser independent of the PyMuPDF/pikepdf
code that wrote the tags. Each marked-content sequence's characters, images and graphics are
attributed to the structure element that owns it, then compared with the element's role.
Findings never block a job; they build the "needs human review" list.
"""
from __future__ import annotations

import hashlib
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import pdfplumber
import pdfplumber.page as _plumb_page
import pikepdf
import pymupdf as fitz
from pikepdf import Dictionary, Name

from app.pipeline import page_pool
from app.verification.integrity import TagIndex, _items
from app.verification.model import Finding, area_boxes

log = logging.getLogger(__name__)

_BOLD = re.compile(r"bold|black|heavy|semibold|demi", re.I)
_SUBSET = re.compile(r"^[A-Z]{6}\+")
_FILENAME = re.compile(r"\.(jpe?g|png|gif|tiff?|bmp|svg|webp|emf|wmf)\b", re.I)
_GENERIC_ALT = {"image", "picture", "photo", "graphic", "figure", "img", "logo", "icon", "chart", "diagram"}
_GENERIC_LINK = re.compile(r"^\s*(click here|here|link|read more|more|this link|learn more|go)\s*\.?\s*$", re.I)
_NUM_LABEL = re.compile(r"^\s*\(?(\d{1,3})[.)]")
_PER_ISSUE_CAP = 25
# Each is a figure for a person to describe (#659: every figure of a document made without AI), so each keeps its row
_UNCAPPED = {"figure has no alt text"}


def _patch_pdfplumber_marked_content() -> None:
    """pdfplumber tracks only the innermost marked-content section and forgets the enclosing MCID
    once a nested section (e.g. a /Span with ActualText around a ligature) closes. Keep a stack so
    every object gets the MCID of its nearest enclosing tagged section, and "Artifact" when any
    enclosing section is an artifact."""
    from pdfplumber.page import PDFPageAggregatorWithMarkedContent as Agg
    from pdfplumber.utils.pdfinternals import decode_text

    if getattr(Agg, "_mc_stack_patch", False):
        return

    def begin_tag(self, tag, props=None):
        stack = self.__dict__.setdefault("_mc_stack", [])
        name = decode_text(tag.name)
        mcid = props.get("MCID") if isinstance(props, dict) else None
        stack.append((name, mcid))
        self._mc_refresh()

    def end_tag(self):
        stack = self.__dict__.setdefault("_mc_stack", [])
        if stack:
            stack.pop()
        self._mc_refresh()

    def _mc_refresh(self):
        stack = self.__dict__.get("_mc_stack", [])
        self.cur_mcid = next((m for _, m in reversed(stack) if m is not None), None)
        if any(t == "Artifact" for t, _ in stack):
            self.cur_tag = "Artifact"
        else:
            self.cur_tag = next((t for t, m in reversed(stack) if m is not None), stack[-1][0] if stack else None)

    Agg.begin_tag, Agg.end_tag, Agg._mc_refresh = begin_tag, end_tag, _mc_refresh
    Agg._mc_stack_patch = True


_patch_pdfplumber_marked_content()


# The glyph fields the checks use (text, its size and font, where it is, which marked content it is in)
_CHAR_KEYS = ("text", "size", "fontname", "x0", "x1", "top", "bottom", "mcid", "tag")

_ALT_LONG = 300
_ALT_LONG_DRAWING = 1000  # above what a labeled figure's description is cut at (ALT_LIMIT_LONG, app/ai/base.py)


_NEAR = 4  # glyph pairs each side looked at for how close a line's letters sit


def _gap(prev: dict, c: dict, near=None) -> bool:
    """Whether a space stands between two glyphs drawn one after the other: the second starts a new line (back to the
    left and lower down; a raised or lowered script carries on to the right), or a gap on the line is a word space drawn
    by moving the pen rather than with a space character: over a quarter of an em, or over 0.18 em where the letters
    around it sit much closer (near(): the narrowest of their gaps). TeX's justified lines set word spaces down to
    about 0.23 em with letters touching (a problem set read "Atrainisinitially"); a heading set with wide letter
    spacing has every gap that wide, and reads as words, not letters. A line that writes its word spaces as
    characters (an OCR layer, whose glyph widths aren't the picture's) has no narrow word space to find."""
    size = max(prev.get("size") or 0.0, c.get("size") or 0.0, 1.0)
    if c["x0"] < prev["x0"] and abs(c["top"] - prev["top"]) > 0.5 * size:
        return not prev["text"].rstrip().endswith("-")  # a word broken at the line's end stays one word
    gap = c["x0"] - prev["x1"]
    if gap > 0.25 * size:
        return True
    return gap > 0.18 * size and near is not None and gap > 3 * near() + 0.05 * size


def _near_gaps(chars: list, i: int, before: list[float] | None) -> float:
    """The narrowest gap between neighbouring glyphs on the line around chars[i]: `before` (the line's last few gaps
    up to it) and the next few after it. Infinite where the line writes space characters (before is None, or one
    follows)."""
    if before is None:
        return float("inf")
    gaps = list(before)
    prev = chars[i]
    for c in chars[i + 1:]:
        if len(gaps) >= len(before) + _NEAR:
            break
        if not c["text"].strip():
            return float("inf")
        if abs(c["top"] - prev["top"]) > 0.5 * max(c.get("size") or 0.0, 1.0) or c["x0"] < prev["x0"]:
            break
        gaps.append(c["x0"] - prev["x1"])
        prev = c
    return max(0.0, min(gaps)) if gaps else float("inf")


def glyph_text(chars: list, limit: int | None = None) -> tuple[str, list[int]]:
    """The text of these glyphs, in drawing order: whitespace collapsed, and a space where a line ends or a gap stands
    for one (a paragraph's lines are drawn without a space between them). Also where each glyph's text starts in it,
    so a place in the text can be found among the glyphs (splitting a tag there). limit: only the first that many
    characters are wanted; the glyphs are read in order, so stopping there gives the same start as reading them all
    (a whole document's text isn't made to keep its first 200 characters)."""
    out: list[str] = []
    starts: list[int] = []
    last_space, prev = True, None
    recent: list[float] | None = []  # the line's last few gaps between glyphs (None: it writes spaces as characters)
    for i, c in enumerate(chars):
        if limit is not None and len(out) > limit:
            return "".join(out)[:limit], starts
        t = c["text"]
        if prev is not None and t.strip():
            same_line = abs(c["top"] - prev["top"]) <= 0.5 * max(c.get("size") or 0.0, 1.0) and c["x0"] >= prev["x0"]
            if not last_space and _gap(prev, c, lambda: _near_gaps(chars, i, recent)):
                out.append(" ")
                last_space = True
            if not same_line:
                recent = []
            elif recent is not None:
                recent = (recent + [c["x0"] - prev["x1"]])[-_NEAR:]
        elif prev is not None and recent is not None and t and not t.strip():
            recent = None
        starts.append(len(out))
        for ch in t:
            if ch.isspace():
                if not last_space:
                    out.append(" ")
                    last_space = True
            else:
                out.append(ch)
                last_space = False
        if t.strip():
            prev = c
    return "".join(out).rstrip(), starts


@dataclass
class Content:
    chars: list = field(default_factory=list)
    images: list = field(default_factory=list)  # (bbox, digest, area)
    graphics: int = 0
    boxes: list = field(default_factory=list)
    page_boxes: list = field(default_factory=list)  # (page 0-based, bbox) for the tag manager

    def add(self, other: "Content") -> None:
        self.chars += other.chars
        self.images += other.images
        self.graphics += other.graphics
        self.boxes += other.boxes
        self.page_boxes += other.page_boxes

    @property
    def text(self) -> str:
        return glyph_text(self.chars)[0]

    def text_upto(self, limit: int) -> str:
        """The first `limit` characters of text, the same as text[:limit], without making the rest."""
        return glyph_text(self.chars, limit)[0][:limit]

    @property
    def bbox(self):
        if not self.boxes:
            return None
        return (min(b[0] for b in self.boxes), min(b[1] for b in self.boxes),
                max(b[2] for b in self.boxes), max(b[3] for b in self.boxes))

    @property
    def size(self) -> float:
        sizes = [round(c["size"] * 2) / 2 for c in self.chars if c["text"].strip()]
        return Counter(sizes).most_common(1)[0][0] if sizes else 0.0

    @property
    def bold(self) -> bool:
        vis = [c for c in self.chars if c["text"].strip()]
        return bool(vis) and sum(1 for c in vis if _BOLD.search(_SUBSET.sub("", c.get("fontname", "")))) / len(vis) > 0.6



_PAINT = {"n", "S", "s", "f", "F", "f*", "B", "B*", "b", "b*"}


_INLINE_IMAGE = re.compile(rb"(?:^|[\s>\]}])BI(?=[\s/])")  # an inline image's opening operator


def _may_draw_images(page: pikepdf.Page, xobjects) -> bool:
    """Whether the page's own content can draw a picture: an image XObject among its resources, or an inline image
    in its stream. A page that can't needs no clip walk; on a long text document that walk was most of the
    checker's own time. A form XObject's pictures aren't drawn by the page's stream and aren't clipped here."""
    if any(isinstance(x, pikepdf.Stream) and x.get("/Subtype") == Name.Image for _, x in xobjects.items()):
        return True
    try:
        data = page.obj.Contents.read_bytes() if isinstance(page.obj.get("/Contents"), pikepdf.Stream) else \
            b"\n".join(c.read_bytes() for c in (page.obj.get("/Contents") or []))
    except (pikepdf.PdfError, AttributeError, TypeError):
        return True  # unreadable: let the walk decide, as before
    return _INLINE_IMAGE.search(data) is not None


def _image_clips(page: pikepdf.Page) -> dict[int | None, list[fitz.Rect | None]]:
    """For each marked-content id, the clip (PDF page space) of each image drawn directly in the page's content, in
    drawing order (None: not clipped). pdfplumber places an image where its matrix puts it and ignores clipping, so
    a picture drawn under a clip (a scanned page's figure is the scan drawn again, clipped to the figure) would seem
    to cover the whole page. Any clipping path counts by its bounds."""
    res = page.obj.get("/Resources") or Dictionary()
    xobjects = res.get("/XObject") or Dictionary()
    props = res.get("/Properties") or Dictionary()
    out: dict[int | None, list[fitz.Rect | None]] = defaultdict(list)
    if not _may_draw_images(page, xobjects):
        return out  # nothing to clip: a page of text alone needn't have its content stream parsed again
    ctm, clip = fitz.Identity, None
    saved: list = []
    path: list[fitz.Point] = []
    clipping = False
    marked: list[int | None] = []

    def nums(operands) -> list[float]:
        return [float(v) for v in operands]

    for operands, op in pikepdf.parse_content_stream(page):
        op = str(op)
        if op == "q":
            saved.append((ctm, clip))
        elif op == "Q":
            if saved:
                ctm, clip = saved.pop()
        elif op == "cm":
            ctm = fitz.Matrix(*nums(operands)) * ctm
        elif op in ("m", "l", "c", "v", "y"):
            v = nums(operands)
            path += [fitz.Point(v[k], v[k + 1]) * ctm for k in range(0, len(v), 2)]
        elif op == "re":
            x, y, w, h = nums(operands)
            path += [fitz.Point(px, py) * ctm for px, py in ((x, y), (x + w, y), (x, y + h), (x + w, y + h))]
        elif op in ("W", "W*"):
            clipping = True
        elif op in _PAINT:
            if clipping and path:
                bounds = fitz.Rect(min(p.x for p in path), min(p.y for p in path),
                                   max(p.x for p in path), max(p.y for p in path))
                clip = bounds if clip is None else (clip & bounds)
            path, clipping = [], False
        elif op == "BMC":
            marked.append(None)
        elif op == "BDC":
            pl = operands[1] if len(operands) > 1 else None
            if isinstance(pl, pikepdf.Name):
                pl = props.get(pl)
            mcid = pl.get("/MCID") if isinstance(pl, Dictionary) else None
            marked.append(int(mcid) if mcid is not None else None)
        elif op == "EMC":
            if marked:
                marked.pop()
        elif op in ("Do", "INLINE IMAGE"):
            if op == "Do":
                x = xobjects.get(operands[0]) if operands else None
                if x is None or x.get("/Subtype") != "/Image":
                    continue
            owner = next((m for m in reversed(marked) if m is not None), None)
            out[owner].append(None if clip is None else fitz.Rect(clip))
    return out


class _FormsDevice(_plumb_page.PDFPageAggregatorWithMarkedContent):
    """pdfplumber's device, also noting which Form XObjects were open when each marked-content sequence began (#423).
    A form numbers its marked content on its own (its tags point at it through /Stm), so an MCID alone doesn't say
    which tag content inside a form belongs to. The forms are noted where the sequence begins, not where a glyph is
    drawn: a Figure marked on the page around a form that marks nothing itself is the page's content."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._forms: list[str] = []
        self.cur_forms: tuple = ()

    def begin_figure(self, name, bbox, matrix):
        self._forms.append(name)
        super().begin_figure(name, bbox, matrix)

    def end_figure(self, name):
        super().end_figure(name)
        if self._forms:
            self._forms.pop()

    def begin_tag(self, tag, props=None):
        super().begin_tag(tag, props)
        self.cur_forms = tuple(self._forms)

    def end_tag(self):
        super().end_tag()
        self.cur_forms = ()

    def tag_cur_item(self):
        super().tag_cur_item()
        if self.cur_item._objs:
            self.cur_item._objs[-1].mcid_forms = self.cur_forms  # type: ignore[attr-defined]


_plumb_page.PDFPageAggregatorWithMarkedContent = _FormsDevice
_plumb_page.ALL_ATTRS.add("mcid_forms")
_UNKNOWN_FORM = ("?",)  # a form that couldn't be followed: its content belongs to no tag, not to the page's MCIDs


def _form_owner(pkpage, forms: tuple, cache: dict):
    """The Form XObject a marked-content sequence is in, by the resource names that led to it (the page's own stream:
    None): its (object, generation), as the tags' /Stm references name it."""
    if not forms:
        return None
    if forms not in cache:
        owner = None
        res = pkpage.obj.get("/Resources") if pkpage is not None else None
        try:
            for name in forms:
                xo = (res.get("/XObject") if isinstance(res, Dictionary) else None) or {}
                form = xo.get("/" + str(name).lstrip("/"))
                if not isinstance(form, pikepdf.Stream) or form.get("/Subtype") != "/Form" or not form.is_indirect:
                    owner = _UNKNOWN_FORM
                    break
                owner = form.objgen
                res = form.get("/Resources") or res
        except (pikepdf.PdfError, AttributeError, TypeError, ValueError):
            owner = _UNKNOWN_FORM
        cache[forms] = owner
    return cache[forms]


@dataclass
class PageContent:
    """What one page holds, for SemanticChecker: by tag (element index), by marked-content item, and its artifacts."""
    page_size: tuple = (0.0, 0.0)
    own: dict = field(default_factory=dict)  # element index -> Content, in the order first seen
    by_mcid: dict = field(default_factory=dict)  # (page, mcid) -> Content
    by_form_mcid: dict = field(default_factory=dict)  # (page, form's objgen, mcid) -> Content (#423)
    artifact: Content | None = None  # None: nothing on the page asked for it
    tables: list = field(default_factory=list)


def _each_page(path, numbers, by_page_mcid: dict, pdf: pikepdf.Pdf | None = None):
    """(page number, PageContent) for each page numbered, in order."""
    own_pdf = pdf is None
    if own_pdf:
        pdf = pikepdf.open(path)
    try:
        with fitz.open(path) as doc, pdfplumber.open(path) as plumb:
            for i in numbers:
                page = plumb.pages[i]
                yield i, _collect_page(i, page, doc[i], pdf.pages[i], by_page_mcid)
                page.close()  # its parsed objects are cached until then: on a long document, every page's
                # would stay in memory to the end
    finally:
        if own_pdf:
            pdf.close()


def collect_pages(path, numbers: list[int], by_page_mcid: dict) -> dict[int, PageContent]:
    """One page_pool worker's share of SemanticChecker._collect (#124)."""
    return dict(_each_page(path, numbers, by_page_mcid))


def _collect_page(i: int, page, fpage: fitz.Page, pkpage, by_page_mcid: dict) -> PageContent:
    out = PageContent()

    def artifact() -> Content:
        if out.artifact is None:
            out.artifact = Content()
        return out.artifact

    vis = fpage.rect
    out.page_size = (vis.width, vis.height)
    top0 = float(page.bbox[1])
    x00 = float(page.bbox[0])

    def vbox(o):
        # pdfplumber gives a rotated page's positions in visual space already (since 0.11): turning them again put a
        # /Rotate 90 page's boxes half a turn off
        return (o["x0"] - x00, o["top"] - top0, o["x1"] - x00, o["bottom"] - top0)

    forms_cache: dict = {}

    def key(o):
        """Where o's marked content is numbered: (page, mcid) in the page's own stream, (page, form, mcid) in a form."""
        owner = _form_owner(pkpage, o.get("mcid_forms") or (), forms_cache)
        return (i, o.get("mcid")) if owner is None else (i, owner, o.get("mcid"))

    def bucket(o) -> Content:
        mcid = o.get("mcid")
        if mcid is None or o.get("tag") == "Artifact":
            return artifact()
        idx = by_page_mcid.get(key(o))
        if idx is None:
            return artifact()
        return out.own.setdefault(idx, Content())

    def piece(o) -> Content | None:
        """The content of the one marked-content item o belongs to (for the tag manager)."""
        mcid = o.get("mcid")
        k = key(o) if mcid is not None else None
        if mcid is None or o.get("tag") == "Artifact" or k not in by_page_mcid:
            return None
        return (out.by_mcid if len(k) == 2 else out.by_form_mcid).setdefault(k, Content())

    for c in page.chars:
        b = vbox(c)
        # Only what the checks read: pdfplumber's own dict has some 25 fields per glyph, which on a
        # 332-page document came to gigabytes (the worker was killed for memory, 2026-10-16)
        where = {"mcid": c.get("mcid"), "tag": c.get("tag"), "mcid_forms": c.get("mcid_forms")}
        c = {k: c.get(k) for k in _CHAR_KEYS} | {"vbox": b}
        dest = bucket(where)
        dest.chars.append(c)
        dest.boxes.append(b)
        dest.page_boxes.append((i, b))
        if (pc := piece(where)) is not None:
            pc.chars.append(c)
            pc.boxes.append(b)
    # Images drawn under a clip show only inside it (see _image_clips); matched to pdfplumber's
    # images per marked-content id, in order, when the two agree on how many there are
    try:
        clips = _image_clips(pkpage)
    except Exception:  # noqa: BLE001 - unreadable content: use the images' full placement
        clips = {}
    counts = Counter(im.get("mcid") for im in page.images)
    seen: Counter = Counter()
    to_page = fpage.transformation_matrix * fpage.rotation_matrix
    for im in page.images:
        b = vbox(im)
        mcid = im.get("mcid")
        listed = clips.get(mcid, [])
        k = seen[mcid]
        seen[mcid] += 1
        if len(listed) == counts[mcid] and listed[k] is not None:
            shown = fitz.Rect(listed[k]) * to_page
            cut = fitz.Rect(b) & shown
            b = tuple(cut) if not cut.is_empty else tuple(shown)
        try:
            raw = im["stream"].get_rawdata() or b""
            digest = hashlib.sha1(raw[:65536]).hexdigest()
        except Exception:  # noqa: BLE001 - unreadable image data: fall back to geometry
            digest = f"{im.get('srcsize')}"
        area = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
        dest = bucket(im)
        dest.images.append((b, digest, area))
        dest.boxes.append(b)
        dest.page_boxes.append((i, b))
        if (pc := piece(im)) is not None:
            pc.images.append((b, digest, area))
            pc.boxes.append(b)
    for g in page.rects + page.curves + page.lines:
        dest = bucket(g)
        dest.graphics += 1
        if dest is not artifact():
            gb = vbox(g)
            dest.boxes.append(gb)
            dest.page_boxes.append((i, gb))
            if (pc := piece(g)) is not None:
                pc.graphics += 1
                pc.boxes.append(gb)
    try:
        out.tables = [(t.bbox, t.extract(), vbox({"x0": t.bbox[0], "top": t.bbox[1], "x1": t.bbox[2],
                                                  "bottom": t.bbox[3]}))
                      for t in page.find_tables()]
    except Exception:  # noqa: BLE001 - table finder is heuristic
        out.tables = []
    return out


class SemanticChecker:
    def __init__(self, path, pdf: pikepdf.Pdf, index: TagIndex, max_pages: int = 400):
        self.path = path
        self.pdf = pdf
        self.index = index
        self.max_pages = max_pages
        self.findings: list[Finding] = []
        self.own: dict[int, Content] = {}  # element index -> content directly referenced
        self.page_size: dict[int, tuple[float, float]] = {}
        self.artifact: dict[int, Content] = defaultdict(Content)
        self.tables: dict[int, list] = {}
        self.skipped: list[str] = []
        self.by_mcid: dict[tuple[int, int], Content] = {}  # (page, mcid) -> content, for the tag manager
        self.by_form_mcid: dict[tuple, Content] = {}  # (page, form's objgen, mcid) -> content inside a form (#423)

    def add(self, severity: str, issue: str, e=None, **kw) -> None:
        if e is not None:
            kw.setdefault("element", e.index)
            kw.setdefault("role", e.role)
            page = self._page_of(e)
            if page is not None:
                kw.setdefault("page", page + 1)
        self.findings.append(Finding(layer="semantic", severity=severity, issue=issue, **kw))

    # ------------------------------------------------------------------ content extraction
    def _collect(self) -> None:
        owner_for_page = {i: og for i, og in enumerate(self.index.page_objgens)}
        # The tag each piece of marked content belongs to: (page, mcid) in a page's own stream, (page, form, mcid) in
        # a Form XObject drawn on it (#423)
        by_page_mcid: dict[tuple, int] = {}
        for (owner, mcid), users in self.index.refs.items():
            page = self.index.page_of_owner.get(owner)
            if page is None:
                continue
            by_page_mcid[(page, mcid) if owner == owner_for_page.get(page) else (page, tuple(owner), mcid)] = users[0]
        count = len(self.pdf.pages)
        pages = min(count, self.max_pages)
        # A long document's pages read by several processes (#124), each page on its own, put back in page order
        workers = page_pool.workers_for(pages)
        done = page_pool.run("app.verification.semantic:collect_pages", self.path, pages, workers, arg=by_page_mcid) \
            if workers > 1 else None
        if done is not None:
            for i in range(pages):
                self._merge(i, done[i])
                done[i] = None  # merged: not held twice
        else:
            for i, part in _each_page(self.path, range(pages), by_page_mcid, self.pdf):
                self._merge(i, part)
        if count > self.max_pages:
            self.skipped.append(f"semantic checks limited to the first {self.max_pages} pages")

    def _merge(self, i: int, part: "PageContent") -> None:
        self.page_size[i] = part.page_size
        if part.artifact is not None:
            self.artifact[i] = part.artifact
        for idx, c in part.own.items():
            self.own.setdefault(idx, Content()).add(c)
        self.by_mcid.update(part.by_mcid)
        self.by_form_mcid.update(part.by_form_mcid)
        self.tables[i] = part.tables

    def _page_of(self, e) -> int | None:
        """Page of an element, or of its first descendant that has one (containers often lack /Pg)."""
        stack = [e.index]
        while stack:
            x = self.index.elements[stack.pop(0)]
            if x.page is not None:
                return x.page
            stack.extend(x.kids)
        return None

    def subtree(self, e) -> Content:
        """Everything under e, in document order (pre-order: its own content, then each child's in turn)."""
        out = Content()
        stack = [e.index]
        while stack:
            k = stack.pop()
            if k in self.own:
                out.add(self.own[k])
            stack.extend(reversed(self.index.elements[k].kids))  # popped last-in first: push the first child last
        return out

    # ------------------------------------------------------------------ checks
    def run(self) -> list[Finding]:
        if not self.index.elements:
            return []
        self._collect()
        els = self.index.elements
        self.body = self._body_size()
        self._headings(els)
        self._paragraphs(els)
        self._figures(els)
        self._artifacts()
        self._tables(els)
        self._lists(els)
        self._links(els)
        self._fields(els)
        self._reading_order(els)
        self._language(els)
        self._levels(els)
        self._cut_off()
        return self._cap(self.findings)

    def _levels(self, els) -> None:
        """Tags at the right level, as PAC checks them (#437, #438): a figure or formula on one page without its
        bounding box, a figure standing as a block without saying so, inline tags straight in a grouping tag, a P in a
        P, a Document inside the tree. One finding per kind, with how many and where."""
        from app.tagging.structure_rules import GROUPING, INLINE

        def attr(e, key) -> bool:
            a = e.obj.get("/A")
            return any(isinstance(d, Dictionary) and key in d for d in (a if isinstance(a, pikepdf.Array) else [a]))

        found: dict[tuple, list] = {}
        for e in els:
            parent = els[e.parent].role if e.parent is not None else None
            if e.role in ("Figure", "Formula") and not attr(e, "/BBox"):
                pages = {pg for pg, _ in self.subtree(e).page_boxes}
                if len(pages) == 1:
                    found.setdefault(("high", "figure on one page without a bounding box"), []).append(e)
            if e.role in ("Figure", "Formula", "Form") and parent in GROUPING and not attr(e, "/Placement"):
                found.setdefault(("review", "figure standing as a block without saying so"), []).append(e)
            if e.role in INLINE and parent in GROUPING:
                found.setdefault(("review", "inline tag standing directly in a grouping tag"), []).append(e)
            if e.role == "P" and parent == "P":
                found.setdefault(("review", "paragraph inside a paragraph"), []).append(e)
            if e.role == "Document" and e.parent is not None:
                found.setdefault(("review", "Document tag inside the tree"), []).append(e)
        actions = {
            "figure on one page without a bounding box": "Its bounding box (BBox) is missing: PAC fails the file on it",
            "figure standing as a block without saying so": "Give it /Placement /Block, or put it in a paragraph",
            "inline tag standing directly in a grouping tag": "Put it in a paragraph (P), or make it one",
            "paragraph inside a paragraph": "Make the outer one a grouping tag (Div), or the inner one a Span",
            "Document tag inside the tree": "Only the top tag is a Document: make this one a Part",
        }
        for (severity, issue), es in found.items():
            pages = sorted({p + 1 for e in es if (p := self._page_of(e)) is not None})
            where = ", ".join(map(str, pages[:8])) + (f" and {len(pages) - 8} more" if len(pages) > 8 else "")
            self.add(severity, issue, es[0], detail=f"{len(es)} tag(s), on page(s) {where}" if pages else
                     f"{len(es)} tag(s)", action=actions[issue], extra={"count": len(es), "pages": pages[:200]})

    def _cut_off(self) -> None:
        """A page whose picture is much larger than the page: part of it can't be seen (a web page or a wide sheet
        "printed" to PDF at the wrong size). Nothing in the file can bring the missing part back, so say so."""
        try:
            doc = fitz.open(self.path)
        except Exception:  # noqa: BLE001 - this check is a courtesy; the others don't depend on it
            return
        try:
            for n, page in enumerate(doc):
                if n >= self.max_pages:
                    break
                pw, ph = page.rect.width, page.rect.height
                worst = 0.0
                for info in page.get_image_info():
                    # Placed in unrotated page space, and the page's size is its visual one
                    x0, y0, x1, y1 = fitz.Rect(info["bbox"]) * page.rotation_matrix
                    area = (x1 - x0) * (y1 - y0)
                    if area < 0.25 * pw * ph:
                        continue  # small pictures bleeding off the edge are a design choice
                    seen = max(0.0, min(x1, pw) - max(x0, 0.0)) * max(0.0, min(y1, ph) - max(y0, 0.0))
                    worst = max(worst, 1 - seen / area)
                if worst >= 0.2:
                    self.add("review", "part of the page is cut off", page=n + 1,
                             detail=f"the page's picture is larger than the page, so about {round(worst * 100)}% of it "
                                    "isn't shown (text and pictures stop at the page's edge)",
                             action="Make the PDF again from the original so the whole page fits (for a web page, "
                                    "print with \"fit to page\" or a smaller scale), then submit that file")
        finally:
            doc.close()

    def _body_size(self) -> float:
        sizes: Counter = Counter()
        for idx, c in self.own.items():
            if self.index.elements[idx].role in ("P", "LBody", "TD", "Span"):
                for ch in c.chars:
                    if ch["text"].strip():
                        sizes[round(ch["size"] * 2) / 2] += 1
        if not sizes:
            for c in self.own.values():
                for ch in c.chars:
                    if ch["text"].strip():
                        sizes[round(ch["size"] * 2) / 2] += 1
        return sizes.most_common(1)[0][0] if sizes else 10.0

    def _ancestors(self, e):
        p = e.parent
        while p is not None:
            yield self.index.elements[p]
            p = self.index.elements[p].parent

    def _headings(self, els) -> None:
        by_level: dict[int, list[float]] = defaultdict(list)
        for e in els:
            if not re.fullmatch(r"H[1-6]", e.role):
                continue
            c = self.subtree(e)
            text = c.text
            if not text:
                continue
            words = _word_count(text)
            size = c.size
            by_level[int(e.role[1])].append(size)
            if size and size < self.body * 1.05 and not c.bold:
                self.add("review", "heading looks like body text", e, text=text[:80],
                         detail=f"{size:g}pt, not bold (body text is {self.body:g}pt)",
                         action="confirm it is a heading, or retag as P", confidence=0.7)
            if words > 25:
                self.add("review", "heading is unusually long", e, text=text[:80], detail=f"{words} words",
                         action="retag as P unless it is a real heading", confidence=0.6)
            if any(a.role in ("TD", "TH") for a in self._ancestors(e)):
                self.add("suggest", "heading inside a table cell", e, text=text[:80],
                         action="use TH or P inside tables", confidence=0.6)
        medians = {lvl: sorted(v)[len(v) // 2] for lvl, v in by_level.items() if v}
        levels = sorted(medians)
        for a, b in zip(levels, levels[1:]):
            if medians[b] > medians[a] * 1.1:
                self.add("suggest", f"H{b} headings are larger than H{a} headings",
                         detail=f"H{a} ≈ {medians[a]:g}pt, H{b} ≈ {medians[b]:g}pt",
                         action="check the heading levels", confidence=0.5)

    def _paragraphs(self, els) -> None:
        for i, e in enumerate(els):
            if e.role != "P" or e.index not in self.own:
                continue
            c = self.own[e.index]
            text = c.text
            words = _word_count(text)
            letters = sum(ch.isalpha() for ch in text)
            if 0 < words <= 12 and letters >= 3 and c.size >= self.body * 1.25 \
                    and not text.endswith((".", ",", ";", ":")):
                self.add("suggest", "short large-type paragraph may be a missed heading", e, text=text[:80],
                         detail=f"{c.size:g}pt vs body {self.body:g}pt", action="consider tagging as a heading",
                         confidence=0.5)

    def _figures(self, els) -> None:
        alts: dict[str, set] = defaultdict(set)
        alt_owner: dict[str, object] = {}
        for e in els:
            if e.role not in ("Figure", "Formula"):
                continue
            c = self.subtree(e)
            alt = str(e.obj.get("/Alt", "") or e.obj.get("/ActualText", "")).strip()
            if not c.images and not c.graphics and not c.chars:
                self.add("review", "figure covers no visible content", e, detail="no image, drawing or text",
                         action="remove the Figure tag or attach it to the right content", confidence=0.8)
            elif e.role == "Figure" and not c.images and c.graphics == 0 and len(c.text) > 40:
                self.add("suggest", "figure contains only text", e, text=c.text[:80],
                         action="tag as text (P/Formula) so it can be read and reflowed", confidence=0.4)
            if not alt:
                # A PDF/UA failure veraPDF reports too, but listed here for a person to describe: a document made
                # without AI (#659), or one whose AI calls failed, has every figure left like this
                if e.role == "Figure" and (c.images or c.graphics or c.chars):
                    self.add("high", "figure has no alt text", e,
                             action="describe it with Edit tag in the tag manager, or mark it decorative if it only "
                                    "decorates; until then a screen reader says nothing for it", confidence=1.0)
                continue
            low = alt.lower().strip(" .")
            if _FILENAME.search(alt) or low in _GENERIC_ALT:
                self.add("review", "alt text is a file name or generic word", e, detail=f'alt: "{alt[:80]}"',
                         action="describe what the image shows", confidence=0.9)
            elif len(alt.split()) < 2 and e.role == "Figure":
                self.add("suggest", "alt text is very short", e, detail=f'alt: "{alt}"',
                         action="check it conveys the image's purpose", confidence=0.4)
            # A figure with labels written on it (a diagram, a reaction scheme) has parts and how they relate to
            # say, which a photograph's few words don't cover: it is long only past what a description may hold.
            # The labels are what tells: on a flattened page the same figure is a picture with its labels as the
            # text layer over it, and has no drawing strokes left to go by.
            labeled_drawing = e.role == "Figure" and bool(c.chars)
            if len(alt) > (_ALT_LONG_DRAWING if labeled_drawing else _ALT_LONG):
                self.add("suggest", "alt text is very long", e, detail=f"{len(alt)} characters",
                         action="shorten; move detail to the body text or a caption", confidence=0.5)
            for _, digest, _ in c.images:
                alts[alt].add(digest)
            alt_owner.setdefault(alt, e)
            sib_text = self._adjacent_caption(e)
            if sib_text and alt.strip().lower() == sib_text.strip().lower():
                self.add("suggest", "alt text repeats the visible caption", e, detail=f'"{alt[:80]}"',
                         action="describe the image; the caption is already read", confidence=0.6)
        for alt, digests in alts.items():
            if len(digests) > 1:
                e = alt_owner[alt]
                self.add("review", "same alt text on different images", e,
                         detail=f'"{alt[:80]}" is used for {len(digests)} different images',
                         action="give each image its own description", confidence=0.85)

    def _adjacent_caption(self, e) -> str | None:
        if e.parent is None:
            return None
        sibs = self.index.elements[e.parent].kids
        pos = sibs.index(e.index) if e.index in sibs else -1
        for k in (pos - 1, pos + 1):
            if 0 <= k < len(sibs):
                s = self.index.elements[sibs[k]]
                if s.role in ("Caption", "P"):
                    t = self.subtree(s).text
                    if re.match(r"^\s*(fig\.?|figure|image|chart|table)\s*\d", t, re.I):
                        return re.sub(r"^\s*(fig\.?|figure|image|chart|table)\s*\d+[.:]?\s*", "", t, flags=re.I)
        return None

    def _artifacts(self) -> None:
        for page, c in self.artifact.items():
            w, h = self.page_size.get(page, (0, 0))
            if not w:
                continue
            page_area = w * h
            tagged = [ch["vbox"] for k, piece in (*self.by_mcid.items(), *self.by_form_mcid.items()) if k[0] == page
                      for ch in piece.chars if ch["text"].strip()]
            for b, _, area in c.images:
                # A picture with the page's tagged text lying over it is that text's background: a scanned page
                # under its OCR text layer (sometimes in several layers), not a picture hidden from readers
                over = sum(1 for v in tagged if b[0] <= (v[0] + v[2]) / 2 <= b[2] and b[1] <= (v[1] + v[3]) / 2 <= b[3])
                if over >= 40 and over >= 0.8 * len(tagged):
                    continue
                if 0.10 * page_area <= area < 0.9 * page_area:
                    self.add("review", "large image marked as decorative (hidden from screen readers)",
                             page=page + 1, detail=f"{area / page_area:.0%} of the page", boxes=area_boxes([b]),
                             action="confirm it is decorative, or tag it as a Figure with alt text", confidence=0.6)
            # The margins are a tenth of the page, and at least an inch: a landscape page is short, and its running
            # header, as far down as a portrait page's, was taken for body text (#665)
            margin = max(0.1 * h, 72.0)
            body = [ch for ch in c.chars if ch["text"].strip()
                    and margin < (ch["vbox"][1] + ch["vbox"][3]) / 2 < h - margin]
            if len(body) > 40:
                sample = glyph_text(body[:80])[0]
                self.add("high", "text in the body of the page is marked as an artifact", page=page + 1,
                         text=sample[:80], detail=f"{len(body)} characters hidden from assistive technology",
                         boxes=area_boxes([ch["vbox"] for ch in body]),
                         action="tag it as content unless it is a watermark or background", confidence=0.7)

    def _tables(self, els) -> None:
        for e in els:
            if e.role != "Table" or e.page is None:
                continue
            rows = self._table_rows(e)
            if not rows:
                continue
            n_rows, n_cols = len(rows), max(len(r) for r in rows)
            cells = [t for r in rows for (_, t) in r]
            has_th = any(role == "TH" for r in rows for (role, _) in r)
            numeric = sum(1 for t in cells if re.search(r"\d", t)) / max(1, len(cells))
            if not has_th and n_rows >= 2 and n_cols >= 2:
                self.add("review", "data table has no header cells", e, detail=f"{n_rows}×{n_cols}",
                         action="mark the header row/column as TH", confidence=0.6 + 0.3 * (numeric > 0.3))
            if n_rows == 1 or n_cols == 1:
                self.add("suggest", "single-row or single-column table may be a layout table", e,
                         detail=f"{n_rows}×{n_cols}", action="if it only positions content, remove the table tags",
                         confidence=0.5)
            box = self.subtree(e).bbox
            detected = self._best_table(e.page, box)
            if detected is None:
                continue
            grid = [[(c or "").strip() for c in row] for row in detected]
            norm = lambda s: re.sub(r"\W+", "", s.lower())  # noqa: E731
            found = {norm(c) for row in grid for c in row if c}
            tagged = [norm(t) for t in cells if t]
            if tagged:
                match = sum(1 for t in tagged if t in found or any(t in f or f in t for f in found if f)) / len(tagged)
                if match < 0.7:
                    self.add("review", "table cell text differs from the table on the page", e,
                             detail=f"{match:.0%} of tagged cells match; tagged {n_rows}×{n_cols}, "
                                    f"detected {len(grid)}×{max(len(r) for r in grid) if grid else 0}",
                             action="check rows/columns and merged cells", confidence=0.6)

    def _table_rows(self, e) -> list[list[tuple[str, str]]]:
        els = self.index.elements
        rows = []
        stack = list(reversed(e.kids))
        while stack:
            k = stack.pop()
            x = els[k]
            if x.role == "TR":
                rows.append([(els[c].role, self.subtree(els[c]).text) for c in x.kids if els[c].role in ("TH", "TD")])
            elif x.role in ("THead", "TBody", "TFoot"):
                stack.extend(reversed(x.kids))
        return [r for r in rows if r]

    def _best_table(self, page: int, box):
        if box is None:
            return None
        best, best_ov = None, 0.0
        for _, extracted, vb in self.tables.get(page, []):
            ov = max(0.0, min(box[2], vb[2]) - max(box[0], vb[0])) * max(0.0, min(box[3], vb[3]) - max(box[1], vb[1]))
            if ov > best_ov:
                best, best_ov = extracted, ov
        area = max(1.0, (box[2] - box[0]) * (box[3] - box[1]))
        return best if best_ov > 0.5 * area else None

    def _lists(self, els) -> None:
        for e in els:
            if e.role != "L":
                continue
            items = [els[k] for k in e.kids if els[k].role == "LI"]
            if len(items) == 1:
                self.add("suggest", "list has a single item", e, text=self.subtree(items[0]).text[:80],
                         action="use a paragraph unless more items belong to this list", confidence=0.4)
            nums = []
            for li in items:
                m = _NUM_LABEL.match(self.subtree(li).text)
                nums.append(int(m.group(1)) if m else None)
            seq = [n for n in nums if n is not None]
            if len(seq) >= 3 and len(seq) == len(nums) and any(b != a + 1 for a, b in zip(seq, seq[1:])):
                self.add("suggest", "list numbering is not sequential", e, detail=", ".join(map(str, seq[:12])),
                         action="check for items split into separate lists or missing items", confidence=0.5)

    def _links(self, els) -> None:
        for e in els:
            if e.role != "Link":
                continue
            text = self.subtree(e).text
            for item in _items(e.obj.get("/K")):
                if isinstance(item, Dictionary) and "/Obj" in item:
                    contents = str(item.Obj.get("/Contents", "")).strip()
                    if contents and _GENERIC_LINK.match(contents):
                        self.add("suggest", "link description is generic", e, detail=f'"{contents}"',
                                 action="describe the destination", confidence=0.7)
                    elif not text and re.match(r"^(Link to )?https?://", contents):
                        self.add("suggest", "link is described only by its URL", e, detail=contents[:80],
                                 action="describe the destination in words", confidence=0.4)
            if text and _GENERIC_LINK.match(text):
                self.add("suggest", "link text is generic", e, text=text, action="make the link text descriptive",
                         confidence=0.7)

    def _fields(self, els) -> None:
        """A form field whose tooltip (what a screen reader announces) is missing or only an internal name (#341)."""
        from app.pipeline.field_labels import looks_internal

        for e in els:
            if e.role != "Form":
                continue
            for item in _items(e.obj.get("/K")):
                if isinstance(item, Dictionary) and "/Obj" in item:
                    w = item.Obj
                    tu = w.get("/TU") or (w.get("/Parent").get("/TU") if w.get("/Parent") is not None else None)
                    name = w.get("/T") or (w.get("/Parent").get("/T") if w.get("/Parent") is not None else None)
                    if looks_internal(str(tu or "")):
                        self.add("review", "form field has no name a person would understand", e,
                                 detail=f'announced as "{tu or name or "(nothing)"}"',
                                 action="give it a label in the tag manager (what a person fills in here)",
                                 confidence=0.8)

    def _reading_order(self, els) -> None:
        per_page: dict[int, list[tuple[int, tuple]]] = defaultdict(list)
        for e in els:  # pre-order = reading order
            c = self.own.get(e.index)
            if c is None or e.page is None or not c.boxes:
                continue
            if c.text or c.images:
                per_page[e.page].append((e.index, c.bbox))
        for page, items in per_page.items():
            if len(items) < 8:
                continue
            rank = {idx: r for r, idx in enumerate(_xy_cut([(i, b) for i, b in items]))}
            seq = [rank[i] for i, _ in items]
            pairs = list(zip(seq, seq[1:]))
            agree = sum(1 for a, b in pairs if a < b) / len(pairs)
            if agree < 0.5:
                self.add("review", "reading order disagrees with the page layout", page=page + 1,
                         detail=f"tag order follows the layout for {agree:.0%} of neighboring items",
                         action="check the order in a screen reader or reading-order tool", confidence=0.55)

    def _language(self, els) -> None:
        try:
            from py3langid.langid import MODEL_FILE, LanguageIdentifier
        except ImportError:
            self.skipped.append("language check skipped: py3langid not installed")
            return
        ident = LanguageIdentifier.from_model_file(MODEL_FILE, norm_probs=True)
        doc_lang = str(self.pdf.Root.get("/Lang", "") or "").split("-")[0].lower()
        if not doc_lang:
            return
        for e in els:
            if e.role not in ("P", "H1", "H2", "H3", "H4", "H5", "H6", "LBody", "TD", "Caption", "Note", "FENote") \
                    or e.index not in self.own:
                continue
            text = self.own[e.index].text
            letters = sum(ch.isalpha() for ch in text)
            if len(text) < 40 or letters / max(1, len(text)) < 0.6:
                continue
            lang_here = str(e.obj.get("/Lang", "") or "")
            for a in self._ancestors(e):
                if lang_here:
                    break
                lang_here = str(a.obj.get("/Lang", "") or "")
            effective = (lang_here or doc_lang).split("-")[0].lower()
            lang, prob = ident.classify(text)
            if prob >= 0.95 and lang != effective:
                self.add("review", "passage in a different language from its tag", e, text=text[:80],
                         detail=f"detected {lang} ({prob:.0%}); tagged {effective}",
                         action=f"set Lang={lang} on this element", confidence=round(prob * 0.8, 2),
                         extra={"detected_lang": lang})

    @staticmethod
    def _cap(findings: list[Finding]) -> list[Finding]:
        """Keep the report readable: at most N findings per issue, plus a count of the rest."""
        by_issue: dict[str, list[Finding]] = defaultdict(list)
        for f in findings:
            by_issue[f.issue].append(f)
        out = []
        for issue, fs in by_issue.items():
            if issue in _UNCAPPED:
                out += fs
                continue
            out += fs[:_PER_ISSUE_CAP]
            if len(fs) > _PER_ISSUE_CAP:
                out.append(Finding(layer="semantic", severity=fs[0].severity,
                                   issue=f"{issue} (and {len(fs) - _PER_ISSUE_CAP} more like this)",
                                   detail=f"{len(fs)} occurrences in total"))
        return out


def _word_count(text: str) -> int:
    """Words, treating letter-spaced display text ("C H A P T E R") as the words it spells."""
    tokens = text.split()
    if tokens and sum(len(t) for t in tokens) / len(tokens) < 1.6:
        return max(1, round(len(text.replace(" ", "")) / 6))
    return len(tokens)


def _xy_cut(items: list[tuple[int, tuple]], depth: int = 0) -> list[int]:
    """Recursive XY-cut: split on the widest horizontal gap first, else the widest vertical gap.
    An independent layout ordering, deliberately not the engine's own heuristic."""
    if len(items) <= 1 or depth > 40:
        return [i for i, _ in sorted(items, key=lambda it: (it[1][1], it[1][0]))]

    def best_gap(axis: int):
        lo, hi = (1, 3) if axis == 1 else (0, 2)
        spans = sorted((b[lo], b[hi]) for _, b in items)
        best, at, reach = 0.0, None, spans[0][1]
        for s, e in spans[1:]:
            if s - reach > best:
                best, at = s - reach, (reach + s) / 2
            reach = max(reach, e)
        return best, at

    gy, ay = best_gap(1)
    gx, ax = best_gap(0)
    if gy >= 2 and (gy >= gx or ax is None) and ay is not None:
        top = [it for it in items if (it[1][1] + it[1][3]) / 2 < ay]
        bottom = [it for it in items if (it[1][1] + it[1][3]) / 2 >= ay]
        if top and bottom:
            return _xy_cut(top, depth + 1) + _xy_cut(bottom, depth + 1)
    if gx >= 6 and ax is not None:
        left = [it for it in items if (it[1][0] + it[1][2]) / 2 < ax]
        right = [it for it in items if (it[1][0] + it[1][2]) / 2 >= ax]
        if left and right:
            return _xy_cut(left, depth + 1) + _xy_cut(right, depth + 1)
    return [i for i, _ in sorted(items, key=lambda it: (it[1][1], it[1][0]))]


def check_semantics(path, pdf: pikepdf.Pdf, index: TagIndex, max_pages: int = 400) -> tuple[list[Finding], list[str]]:
    checker = SemanticChecker(path, pdf, index, max_pages)
    findings = checker.run()
    return findings, checker.skipped
