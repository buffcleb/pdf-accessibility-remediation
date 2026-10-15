"""Non-destructive content-stream tagging.

Walks a page (or form XObject) content stream while tracking the CTM and text matrices,
works out where each text-showing operator, image, and painted path lands on the page,
and wraps it in a marked-content sequence:

    /P <</MCID 3>> BDC  ...original operators...  EMC
    /Artifact <</Type /Pagination>> BDC  ...  EMC

Only BDC/EMC operators are inserted (or, in rebuild mode, removed). No painting, text or
state operator is changed, so the rendered page is identical.

Content already inside an MCID or Artifact sequence is passed through untouched, which is
how valid existing tags are retained.
"""
from __future__ import annotations

import re

from dataclasses import dataclass, field
from typing import Callable, Iterable

import pikepdf
from pikepdf import ContentStreamInlineImage, ContentStreamInstruction, Dictionary, Name, Operator

from app.pipeline.models import BBox

Matrix = tuple[float, float, float, float, float, float]
IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

# Resolver(kind, bbox_in_page_space, xref) -> (target_key, mc_tag) ; key "artifact:<Type>" marks an artifact.
Resolver = Callable[[str, BBox, int], tuple[str, str]]

_PATH_CONSTRUCT = {"m", "l", "c", "v", "y", "h", "re"}
_PATH_PAINT = {"S", "s", "f", "F", "f*", "B", "B*", "b", "b*"}
_PATH_FILL = {"f", "F", "f*", "B", "B*", "b", "b*"}
_CLIP = {"W", "W*"}
_TEXT_SHOW = {"Tj", "TJ", "'", '"'}
_BARRIERS = {"q", "Q", "BT", "ET", "BDC", "BMC", "EMC", "BX", "EX"}


def mat_mul(m1: Matrix, m2: Matrix) -> Matrix:
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


def apply(m: Matrix, x: float, y: float) -> tuple[float, float]:
    return (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])


def bbox_of_points(points: Iterable[tuple[float, float]]) -> BBox | None:
    pts = list(points)
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _num(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class StreamMarks:
    """MCIDs created in one content stream (a page, or a form XObject drawn on that page)."""
    owner: pikepdf.Object  # page dict or form XObject stream
    is_form: bool
    mcids: list[tuple[int, str]] = field(default_factory=list)  # (mcid, target_key) created by us
    existing: dict[int, BBox] = field(default_factory=dict)  # pre-existing MCID -> bbox of its content
    existing_kinds: dict[int, set] = field(default_factory=dict)  # pre-existing MCID -> what it draws (text, image…)
    # Pre-existing /Artifact sequences, numbered in stream order: their /Type and /Subtype, and how much of their text
    # the layout calls body text (points of width), for releasing body text a producer hid as an artifact
    artifacts: dict[int, dict] = field(default_factory=dict)
    # Drawing order, for text hidden under shapes drawn over it: (z, pre-existing MCID or None, bbox, visible) for
    # each piece of text, and (z, bbox) for each opaque fill (a filled shape, an image without transparency)
    text_units: list[tuple] = field(default_factory=list)
    fills: list[tuple] = field(default_factory=list)


@dataclass
class MarkResult:
    streams: list[StreamMarks] = field(default_factory=list)
    artifacts: int = 0
    tagged_units: int = 0
    existing_units: int = 0
    stripped_marks: int = 0
    forms_wrapped: int = 0
    released_artifacts: int = 0  # existing artifact sequences unwrapped so their body text gets tagged
    # Characters shown inside a figure or as decoration: font (object number, generation) -> character codes.
    # The ones with no Unicode meaning (note heads, pieces of a drawn line) get private-use values afterwards
    picture_glyphs: dict[tuple[int, int], set[int]] = field(default_factory=dict)
    # The same for characters shown in ordinary text, and how many characters each font showed in either place: a
    # font nearly all of whose characters are in figures is a font of pictures wherever else it turns up
    body_glyphs: dict[tuple[int, int], set[int]] = field(default_factory=dict)
    glyph_counts: dict[tuple[int, int], list[int]] = field(default_factory=dict)  # font -> [in pictures, in text]


def mcid_of(item) -> int | None:
    """The marked-content id an item names, or None. The item is a bare id, or a dictionary with /MCID (a BDC's
    properties, a marked-content reference in a tree); an id that isn't an integer is the file's error and names
    nothing. (The verification layer has its own copy: it shares no code with the writer.)"""
    value = item.get("/MCID") if isinstance(item, Dictionary) else item
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def existing_mcids(instructions) -> set[int]:
    found = set()
    for ins in instructions:
        if isinstance(ins, ContentStreamInstruction) and str(ins.operator) == "BDC" and len(ins.operands) == 2:
            mcid = mcid_of(ins.operands[1])
            if mcid is not None:
                found.add(mcid)
    return found


class _GState:
    __slots__ = ("ctm", "clip", "render", "opaque")

    def __init__(self, ctm: Matrix, clip: BBox | None = None, render: int = 0, opaque: bool = True):
        self.ctm = ctm
        self.clip = clip  # page-space bounds of the clipping path, if one is set
        self.render = render  # text render mode (3: invisible, as in an OCR text layer)
        self.opaque = opaque  # fills cover what's under them (no transparency or soft mask in the ExtGState)


def _clip_to(bbox: BBox | None, clip: BBox | None) -> BBox | None:
    """What of bbox can show through the clip (the clip itself if they don't meet: nothing of it shows)."""
    if bbox is None or clip is None:
        return bbox
    x0, y0 = max(bbox[0], clip[0]), max(bbox[1], clip[1])
    x1, y1 = min(bbox[2], clip[2]), min(bbox[3], clip[3])
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else clip


class ContentMarker:
    def __init__(
        self,
        pdf: pikepdf.Pdf,
        page: pikepdf.Page,
        user_to_page: Matrix,
        resolver: Resolver,
        *,
        strip_existing: bool = False,
        recursable_forms: set[tuple[int, int]] | None = None,
        release_artifacts: dict[tuple[int, int], set[int]] | None = None,
    ):
        self.pdf = pdf
        self.page = page
        self.user_to_page = user_to_page
        self.resolve = resolver
        self.strip_existing = strip_existing
        self.recursable_forms = recursable_forms or set()
        # Existing /Artifact sequences to unwrap (owner objgen -> sequence numbers): their content is tagged afresh
        self.release_artifacts = release_artifacts or {}
        self.result = MarkResult()
        self._width_cache: dict = {}
        self._z = 0  # drawing order across the page and the forms drawn on it
        # The forms being tagged from the inside right now: one that draws itself (or two that draw each other)
        # would otherwise be entered again and again until Python's recursion limit
        self._open_forms: set[tuple[int, int]] = set()

    # ------------------------------------------------------------------ public
    def run(self) -> MarkResult:
        instructions = list(pikepdf.parse_content_stream(self.page.obj))
        resources = self.page.obj.get("/Resources") or self._inherited_resources()
        new_ops, marks = self._process(instructions, IDENTITY, resources, owner=self.page.obj, is_form=False)
        self.page.obj.Contents = self.pdf.make_stream(pikepdf.unparse_content_stream(new_ops))
        self.result.streams.insert(0, marks)
        return self.result

    # ------------------------------------------------------------------ internals
    def _inherited_resources(self) -> pikepdf.Object:
        return inherited_resources(self.page.obj)

    def _to_page_bbox(self, ctm: Matrix, pts: list[tuple[float, float]]) -> BBox | None:
        m = mat_mul(ctm, self.user_to_page)
        return bbox_of_points(apply(m, x, y) for x, y in pts)

    def _process(self, instructions, base_ctm: Matrix, resources, owner, is_form: bool):
        existing = existing_mcids(instructions) if not self.strip_existing else set()
        next_mcid = (max(existing) + 1) if existing else 0
        marks = StreamMarks(owner=owner, is_form=is_form)
        out: list = []

        gs = _GState(base_ctm)
        gs_stack: list[_GState] = []
        tm: Matrix = IDENTITY
        tlm: Matrix = IDENTITY
        font_size = 0.0
        h_scale = 1.0
        leading = 0.0
        char_space = 0.0
        word_space = 0.0
        two_byte = False
        widths: _Widths | None = None
        fdict = None
        fonts = resources.get("/Font") or Dictionary()
        xobjects = resources.get("/XObject") or Dictionary()

        # marked-content nesting of the ORIGINAL stream: entries are (is_tagging_mark, stripped, mcid, artifact seq)
        mc_stack: list[tuple[bool, bool, int | None, int | None]] = []
        art_seq = 0
        release = self.release_artifacts.get(owner.objgen, set()) if owner.is_indirect else set()
        open_key: str | None = None
        path_buf: list = []
        path_pts: list[tuple[float, float]] = []

        def inside_existing() -> bool:
            return any(is_tag and not stripped for is_tag, stripped, _, _ in mc_stack)

        def current_existing_mcid() -> int | None:
            for is_tag, stripped, mcid, _ in reversed(mc_stack):
                if mcid is not None and not stripped:
                    return mcid
            return None

        def current_artifact() -> int | None:
            """The existing artifact sequence this content is directly in (not inside a tagged MCID)."""
            for is_tag, stripped, mcid, seq in reversed(mc_stack):
                if is_tag and not stripped:
                    return seq
            return None

        def close() -> None:
            nonlocal open_key
            if open_key is not None:
                out.append(ContentStreamInstruction([], Operator("EMC")))
                open_key = None

        def open_for(key: str, tag: str) -> None:
            nonlocal open_key, next_mcid
            if open_key == key:
                return
            close()
            if key.startswith("artifact:"):
                art_type = key.split(":", 1)[1] or "Layout"
                props = Dictionary(Type=Name("/" + art_type))
                out.append(ContentStreamInstruction([Name("/Artifact"), props], Operator("BDC")))
                self.result.artifacts += 1
            else:
                mcid = next_mcid
                next_mcid += 1
                marks.mcids.append((mcid, key))
                out.append(ContentStreamInstruction([Name("/" + tag), Dictionary(MCID=mcid)], Operator("BDC")))
                self.result.tagged_units += 1
            open_key = key

        def emit_unit(kind: str, bbox: BBox | None, ops: list, xref: int = 0, covers: bool = True,
                      glyphs: bool = False) -> None:
            if bbox is not None:
                self._z += 1
                if kind == "text":
                    marks.text_units.append((self._z, current_existing_mcid() if inside_existing() else None, bbox,
                                             gs.render not in (3, 7)))
                elif gs.opaque and covers and (kind == "image" or (kind == "path" and str(ops[-1].operator) in _PATH_FILL)):
                    shown = _clip_to(bbox, gs.clip)
                    if shown is not None:
                        marks.fills.append((self._z, shown))
            if inside_existing():
                self.result.existing_units += 1
                mcid = current_existing_mcid()
                if mcid is not None:
                    marks.existing_kinds.setdefault(mcid, set()).add(kind)
                seq = current_artifact()
                if seq is not None and bbox is not None:
                    info = marks.artifacts[seq]
                    prev = info["bbox"]
                    info["bbox"] = bbox if prev is None else (min(prev[0], bbox[0]), min(prev[1], bbox[1]),
                                                              max(prev[2], bbox[2]), max(prev[3], bbox[3]))
                    info["kinds"].add(kind)
                # Visible text only: an invisible text layer (render mode 3 or 7) rightly stays an artifact
                if seq is not None and kind == "text" and bbox is not None and gs.render not in (3, 7):
                    key, _ = self.resolve("text", bbox, 0)
                    if not key.startswith("artifact:"):
                        marks.artifacts[seq]["body"] += bbox[2] - bbox[0]
                if mcid is not None and bbox is not None:
                    prev = marks.existing.get(mcid)
                    marks.existing[mcid] = bbox if prev is None else (
                        min(prev[0], bbox[0]), min(prev[1], bbox[1]), max(prev[2], bbox[2]), max(prev[3], bbox[3]))
                out.extend(ops)
                return
            if bbox is None:
                key, tag = "artifact:Layout", "Artifact"
            else:
                key, tag = self.resolve(kind, bbox, xref)
            if glyphs and fdict is not None and fdict.is_indirect and not two_byte:
                picture = tag == "Figure" or key.startswith("artifact:")
                codes = (self.result.picture_glyphs if picture else self.result.body_glyphs).setdefault(fdict.objgen, set())
                counts = self.result.glyph_counts.setdefault(fdict.objgen, [0, 0])
                operand = ops[-1].operands[-1] if ops[-1].operands else None
                for it in (operand if isinstance(operand, pikepdf.Array) else [operand]):
                    if isinstance(it, pikepdf.String):
                        codes.update(bytes(it))
                        counts[0 if picture else 1] += len(bytes(it))
            open_for(key, tag)
            out.extend(ops)

        def glyph_advance(c: int) -> float:
            """One glyph's advance before horizontal scaling: the font's width where it has one (half an em
            otherwise), the character spacing, and the word spacing after a single-byte space."""
            w = widths.get(c) if widths is not None else None
            adv = (w / 1000.0 * font_size if w is not None else 0.5 * font_size) + char_space
            if not two_byte and c == 32:
                adv += word_space
            return adv

        def glyph_runs(operand) -> list[tuple[int, int, float, float, bool]]:
            """Each glyph of a show operand: (item index, byte offset, start x, end x, is a space), in text
            space from the current pen position; a position adjustment moves the pen between glyphs."""
            out_g = []
            x = 0.0
            items = operand if isinstance(operand, pikepdf.Array) else [operand]
            step = 2 if two_byte else 1
            for n, it in enumerate(items):
                if isinstance(it, pikepdf.String):
                    raw = bytes(it)
                    for i in range(0, len(raw) - step + 1, step):
                        c = int.from_bytes(raw[i:i + step], "big")
                        adv = glyph_advance(c) * h_scale
                        out_g.append((n, i, x, x + adv, not two_byte and c == 32))
                        x += adv
                else:
                    x -= _num(it) / 1000.0 * font_size * h_scale
            return out_g

        def text_advance(operand) -> float:
            """Advance (text space) of a whole show operand: where the pen ends up."""
            total = 0.0
            items = operand if isinstance(operand, pikepdf.Array) else [operand]
            step = 2 if two_byte else 1
            for it in items:
                if isinstance(it, pikepdf.String):
                    raw = bytes(it)
                    for i in range(0, len(raw) - step + 1, step):
                        total += glyph_advance(int.from_bytes(raw[i:i + step], "big"))
                else:
                    total -= _num(it) / 1000.0 * font_size
            return total * h_scale

        def target_at(x0: float, x1: float) -> str:
            fs = font_size or 1.0
            m = mat_mul(mat_mul(tm, gs.ctm), self.user_to_page)
            box = bbox_of_points(apply(m, x, y) for x, y in ((x0, -0.2 * fs), (x1, -0.2 * fs), (x0, 0.8 * fs), (x1, 0.8 * fs)))
            owner = getattr(self.resolve, "text_owner", None)
            return (owner(box) if owner is not None and box is not None else None) or ""

        def split_by_target(ins, op: str, operand) -> list | None:
            """One operator drawing text that belongs to two blocks ("... electrons (b) 4 × 10³²" where the
            producer drew both list items in one run) is shown as several, one per block, so each part is
            tagged with its own element. Same glyphs, same positions."""
            # Only with the font's real glyph widths: estimated positions drift along a line and would
            # put its last words into whatever sits next to it
            if inside_existing() or widths is None:
                return None
            gl = glyph_runs(operand)
            if len(gl) < 2:
                return None
            first, end = target_at(gl[0][2], gl[0][3]), target_at(gl[-1][2], gl[-1][3])
            if not first or not end or first == end:
                return None
            keys, last = [], first
            for g in gl:
                # spaces and glyphs no block clearly contains stay with the part before them
                k = (None if g[4] else target_at(g[2], g[3])) or last
                keys.append(k)
                last = k
            cuts = [i for i in range(1, len(gl)) if keys[i] != keys[i - 1]]
            if not cuts:
                return None
            items = list(operand) if isinstance(operand, pikepdf.Array) else [operand]
            bounds = [(gl[i][0], gl[i][1]) for i in cuts]  # (item, byte) where each new part starts
            parts: list[list] = [[]]
            b = 0
            for n, it in enumerate(items):
                if not isinstance(it, pikepdf.String):
                    # an adjustment right before a cut belongs to the next part (it moves that part's pen)
                    if b < len(bounds) and bounds[b] == (n + 1, 0) and parts[-1]:
                        parts.append([])
                        b += 1
                    parts[-1].append(it)
                    continue
                raw, start = bytes(it), 0
                while b < len(bounds) and bounds[b][0] == n:
                    cut = bounds[b][1]
                    if cut > start:
                        parts[-1].append(pikepdf.String(raw[start:cut]))
                    if parts[-1]:
                        parts.append([])
                    start = cut
                    b += 1
                if start < len(raw):
                    parts[-1].append(pikepdf.String(raw[start:]))
            parts = [p for p in parts if p]
            if len(parts) < 2:
                return None
            if op == "TJ":
                return [ContentStreamInstruction([pikepdf.Array(p)], Operator("TJ")) for p in parts]
            # Tj, ' and ": the first part keeps the operator (its line move and spacing), the rest are Tj
            first = ContentStreamInstruction(list(ins.operands[:-1]) + [parts[0][0]], ins.operator)
            return [first] + [ContentStreamInstruction([p[0]], Operator("Tj")) for p in parts[1:]]

        def show_unit(ins, operand, split: bool = True) -> None:
            """Tag one text-showing operator (or one piece of a split TJ) and advance the text position."""
            nonlocal tm
            if split:
                sub = split_by_target(ins, str(ins.operator), operand)
                if sub:
                    for piece in sub:
                        show_unit(piece, piece.operands[-1], split=False)
                    return
            adv = text_advance(operand)
            items = list(operand) if isinstance(operand, pikepdf.Array) else [operand]
            lead = 0.0  # a leading position adjustment moves the pen before anything is drawn
            for it in items:
                if isinstance(it, pikepdf.String):
                    break
                lead -= _num(it) / 1000.0 * font_size * h_scale
            fs = font_size or 1.0
            trm = mat_mul(tm, gs.ctm)
            corners = [(lead, -0.2 * fs), (adv, -0.2 * fs), (lead, 0.8 * fs), (adv, 0.8 * fs)]
            m = mat_mul(trm, self.user_to_page)
            bbox = bbox_of_points(apply(m, x, y) for x, y in corners)
            # A drawing font's characters are strokes (LaTeX's picture lines and circles): placed as drawings are,
            # in the figure they lie in or as decoration, never as a paragraph
            emit_unit("path" if _is_drawing_font(fdict) else "text", bbox, [ins], glyphs=True)
            tm = mat_mul((1, 0, 0, 1, adv, 0), tm)

        for ins in instructions:
            if isinstance(ins, ContentStreamInlineImage):
                emit_unit("image", self._to_page_bbox(gs.ctm, [(0, 0), (1, 0), (0, 1), (1, 1)]), [ins], 0)
                continue

            op = str(ins.operator)
            args = list(ins.operands)

            # ---- path construction is buffered until it is painted --------------------
            if op in _PATH_CONSTRUCT or op in _CLIP:
                path_buf.append(ins)
                if op == "re" and len(args) == 4:
                    x, y, w, h = (_num(a) for a in args)
                    path_pts += [(x, y), (x + w, y + h)]
                elif op in ("m", "l") and len(args) == 2:
                    path_pts.append((_num(args[0]), _num(args[1])))
                elif op in ("c", "v", "y"):
                    vals = [_num(a) for a in args]
                    path_pts += list(zip(vals[0::2], vals[1::2]))
                continue
            if op in _PATH_PAINT:
                ops = path_buf + [ins]
                bbox = self._to_page_bbox(gs.ctm, path_pts)
                if any(str(i.operator) in _CLIP for i in path_buf):
                    gs.clip = _clip_to(bbox, gs.clip)
                path_buf, path_pts = [], []
                emit_unit("path", bbox, ops)
                continue
            if op == "n":  # end path without painting (clipping only): nothing visible to tag
                ops = path_buf + [ins]
                if any(str(i.operator) in _CLIP for i in path_buf):
                    # Images drawn under a clip are placed where they show: a scanned page's figure is the
                    # scan drawn again, clipped to the figure (pipeline/scan_figures.py)
                    gs.clip = _clip_to(self._to_page_bbox(gs.ctm, path_pts), gs.clip)
                path_buf, path_pts = [], []
                out.extend(ops)
                continue

            # ---- barriers: never let our sequences straddle these -----------------------
            if op in _BARRIERS:
                if op in ("BDC", "BMC"):
                    is_tag = False
                    mcid = None
                    seq = None
                    if op == "BDC" and len(args) == 2 and isinstance(args[1], Dictionary) and "/MCID" in args[1]:
                        mcid = mcid_of(args[1])
                        if mcid is None:
                            # An id that isn't an integer names nothing: the key goes, and what the section holds
                            # is tagged afresh like any untagged content
                            args[1] = Dictionary({k: v for k, v in args[1].items() if k != "/MCID"})
                            ins = ContentStreamInstruction(args, ins.operator)
                        else:
                            is_tag = True
                    if args and isinstance(args[0], Name) and str(args[0]) == "/Artifact":
                        is_tag = True
                        if mcid is None and not self.strip_existing:
                            seq = art_seq
                            art_seq += 1
                            props = args[1] if len(args) == 2 and isinstance(args[1], Dictionary) else Dictionary()
                            marks.artifacts[seq] = {"type": str(props.get("/Type", ""))[1:],
                                                    "subtype": str(props.get("/Subtype", ""))[1:], "body": 0.0,
                                                    "bbox": None, "kinds": set(),
                                                    "nested": inside_existing()}
                    released = seq is not None and seq in release
                    strip = is_tag and (self.strip_existing or released)
                    if released:
                        self.result.released_artifacts += 1
                    close()
                    mc_stack.append((is_tag, strip, mcid, seq))
                    if strip:
                        self.result.stripped_marks += 1
                        continue
                    out.append(ins)
                    continue
                if op == "EMC":
                    close()
                    _, strip, _, _ = mc_stack.pop() if mc_stack else (False, False, None, None)
                    if strip:
                        continue
                    out.append(ins)
                    continue
                close()
                if op == "q":
                    gs_stack.append(_GState(gs.ctm, gs.clip, gs.render, gs.opaque))
                elif op == "Q":
                    if gs_stack:
                        gs = gs_stack.pop()
                elif op == "BT":
                    tm = tlm = IDENTITY
                out.append(ins)
                continue

            # ---- graphics / text state ---------------------------------------------------
            if op == "cm" and len(args) == 6:
                gs.ctm = mat_mul(tuple(_num(a) for a in args), gs.ctm)  # type: ignore[arg-type]
            elif op == "Tm" and len(args) == 6:
                tm = tlm = tuple(_num(a) for a in args)  # type: ignore[assignment]
            elif op in ("Td", "TD") and len(args) == 2:
                tx, ty = _num(args[0]), _num(args[1])
                if op == "TD":
                    leading = -ty
                tm = tlm = mat_mul((1, 0, 0, 1, tx, ty), tlm)
            elif op == "T*":
                tm = tlm = mat_mul((1, 0, 0, 1, 0, -leading), tlm)
            elif op == "TL" and args:
                leading = _num(args[0])
            elif op == "Tr" and args:
                gs.render = int(_num(args[0]))
            elif op == "gs" and args and isinstance(args[0], Name):
                ext = (resources.get("/ExtGState") or Dictionary()).get(args[0])
                if isinstance(ext, Dictionary):
                    smask = ext.get("/SMask")
                    if "/ca" in ext or smask is not None:
                        gs.opaque = _num(ext.get("/ca", 1)) >= 0.999 and (smask is None or smask == Name("/None"))
            elif op == "Tz" and args:
                h_scale = _num(args[0]) / 100.0
            elif op == "Tc" and args:
                char_space = _num(args[0])
            elif op == "Tw" and args:
                word_space = _num(args[0])
            elif op == "Tf" and len(args) == 2:
                font_size = _num(args[1])
                fdict = fonts.get(args[0]) if isinstance(args[0], Name) else None
                two_byte = bool(fdict is not None and fdict.get("/Subtype") == Name.Type0)
                widths = _font_widths(fdict, self._width_cache) if fdict is not None else None

            # ---- text showing --------------------------------------------------------------
            if op in _TEXT_SHOW:
                if op in ("'", '"'):
                    if op == '"' and len(args) == 3:
                        word_space, char_space = _num(args[0]), _num(args[1])
                    tm = tlm = mat_mul((1, 0, 0, 1, 0, -leading), tlm)
                operand = args[-1] if args else pikepdf.String("")
                pieces = _split_tj(operand) if op == "TJ" else None
                if pieces:
                    # One TJ drawing several things far apart ("(b) 2710" ... "(c)") is shown as several
                    # TJs so each can be tagged where it belongs. Same glyphs, same positions.
                    for piece in pieces:
                        show_unit(ContentStreamInstruction([piece], Operator("TJ")), piece)
                    continue
                show_unit(ins, operand)
                continue

            # ---- XObjects --------------------------------------------------------------------
            if op == "Do" and args:
                xobj = xobjects.get(args[0])
                subtype = xobj.get("/Subtype") if xobj is not None else None
                if subtype == Name.Image:
                    bbox = _clip_to(self._to_page_bbox(gs.ctm, [(0, 0), (1, 0), (0, 1), (1, 1)]), gs.clip)
                    # An image with its own transparency doesn't cover what's under it
                    emit_unit("image", bbox, [ins], xobj.objgen[0], covers="/SMask" not in xobj and "/Mask" not in xobj)
                    continue
                if subtype == Name.Form:
                    fm = tuple(_num(v) for v in xobj.get("/Matrix", [1, 0, 0, 1, 0, 0]))
                    form_ctm = mat_mul(fm, gs.ctm)  # type: ignore[arg-type]
                    if (not inside_existing() and xobj.objgen in self.recursable_forms
                            and xobj.objgen not in self._open_forms
                            and "/StructParents" not in xobj and "/StructParent" not in xobj):
                        close()
                        self._tag_form(xobj, form_ctm, resources)
                        out.append(ins)
                        continue
                    fb = [_num(v) for v in xobj.get("/BBox", [0, 0, 0, 0])]
                    bbox = self._to_page_bbox(form_ctm, [(fb[0], fb[1]), (fb[2], fb[3]), (fb[0], fb[3]), (fb[2], fb[1])])
                    self.result.forms_wrapped += 1
                    # A form that only draws lines and fills (crop marks, a box behind a stamp), with any text in
                    # it already marked as an artifact, is furniture: it's placed by what it draws, as paths are (a
                    # figure it lies in, else a Layout artifact). Its /BBox is often the whole page, which would hand
                    # it to whatever text it overlaps most, and tagging it would put its artifact inside a tag.
                    drawn = None if inside_existing() else _furniture_extent(xobj)
                    if drawn is not None:
                        emit_unit("path", self._to_page_bbox(form_ctm, drawn) if drawn else bbox, [ins])
                        continue
                    emit_unit("form", bbox, [ins])
                    continue
                emit_unit("form", None, [ins])
                continue

            if op == "sh":
                emit_unit("shading", None, [ins])
                continue

            # neutral operator (state change): stays inside whatever sequence is open
            out.append(ins)

        close()
        out.extend(path_buf)  # dangling path ops (malformed stream) are kept verbatim
        return out, marks

    def _tag_form(self, form: pikepdf.Object, ctm: Matrix, parent_resources) -> None:
        instructions = list(pikepdf.parse_content_stream(form))
        resources = form.get("/Resources") or parent_resources
        self._open_forms.add(form.objgen)
        try:
            new_ops, marks = self._process(instructions, ctm, resources, owner=form, is_form=True)
        finally:
            self._open_forms.discard(form.objgen)
        form.write(pikepdf.unparse_content_stream(new_ops))
        if marks.mcids:
            self.result.streams.append(marks)


def inherited_resources(page: pikepdf.Object) -> pikepdf.Object:
    """A page's /Resources, or the nearest ancestor's. A /Parent chain is the file's to write: one that loops (a
    page naming itself, two nodes naming each other) would be walked for ever, so each node is visited once."""
    node, seen = page, set()
    while isinstance(node, Dictionary):
        if "/Resources" in node:
            return node.Resources
        if node.is_indirect:
            if node.objgen in seen:
                break
            seen.add(node.objgen)
        node = node.get("/Parent")
    return Dictionary()


def _furniture_extent(form: pikepdf.Object) -> list[tuple[float, float]] | None:
    """For a form XObject that draws only paths (and text or pictures already inside its own /Artifact sections):
    the points of what it paints, in its own space ([] if it paints nothing). None for any other form: one with
    text or pictures of its own, tagged content, another form inside it, or a stream that can't be read."""
    try:
        instructions = list(pikepdf.parse_content_stream(form))
    except pikepdf.PdfError:
        return None
    ctm: Matrix = (1, 0, 0, 1, 0, 0)
    stack: list[Matrix] = []
    marked: list[bool] = []  # open marked-content sections: whether each is an /Artifact
    path: list[tuple[float, float]] = []
    painted: list[tuple[float, float]] = []
    for ins in instructions:
        if not isinstance(ins, ContentStreamInstruction):
            return None  # an inline image
        op, args = str(ins.operator), ins.operands
        if op == "q":
            stack.append(ctm)
        elif op == "Q":
            ctm = stack.pop() if stack else ctm
        elif op == "cm" and len(args) == 6:
            ctm = mat_mul(tuple(_num(v) for v in args), ctm)  # type: ignore[arg-type]
        elif op in ("BDC", "BMC"):
            if op == "BDC" and len(args) == 2 and isinstance(args[1], Dictionary) and "/MCID" in args[1]:
                return None  # tagged content of its own
            marked.append(bool(args) and str(args[0]) == "/Artifact")
        elif op == "EMC":
            if marked:
                marked.pop()
        elif op in ("Tj", "TJ", "'", '"', "Do", "sh", "BI"):
            if not any(marked):
                return None  # text, a picture or a shading that isn't an artifact
        elif op == "re" and len(args) == 4:
            x, y, w, h = (_num(v) for v in args)
            path += [apply(ctm, x, y), apply(ctm, x + w, y), apply(ctm, x, y + h), apply(ctm, x + w, y + h)]
        elif op in ("m", "l") and len(args) == 2:
            path.append(apply(ctm, _num(args[0]), _num(args[1])))
        elif op in ("c", "v", "y") and len(args) in (4, 6):
            nums = [_num(v) for v in args]
            path += [apply(ctm, nums[k], nums[k + 1]) for k in range(0, len(nums), 2)]
        elif op in _PATH_PAINT:
            painted += path
            path = []
        elif op == "n":
            path = []
    return painted


_Widths = dict  # character code -> glyph width in thousandths of an em
MAX_CID = 65535  # a CID is two bytes; a /W range past this is a malformed (or hostile) file
SPLIT_GAP = -1000  # a TJ adjustment moving the pen right by at least one em separates two pieces


_DRAWING_FONTS = re.compile(r"^(?:[A-Z]{6}\+)?(?:LINEW?|LCIRCLEW?)\d+$", re.IGNORECASE)


def _is_drawing_font(font) -> bool:
    """LaTeX's picture-mode fonts: every character is a piece of a slanted line, an arrow head or an arc."""
    return font is not None and bool(_DRAWING_FONTS.match(str(font.get("/BaseFont", ""))[1:]))


def _font_widths(font, cache: dict) -> _Widths | None:
    """Glyph widths of a simple font (/FirstChar + /Widths) or a CID font (/W, /DW). cache belongs to
    one document (object numbers mean nothing across documents)."""
    key = font.objgen if font.is_indirect else None
    if key is not None and key in cache:
        return cache[key]
    out: dict[int, float] = {}
    try:
        if font.get("/Subtype") == Name.Type0:
            desc = font.DescendantFonts[0]
            default = _num(desc.get("/DW", 1000))
            w = desc.get("/W")
            items = list(w) if isinstance(w, pikepdf.Array) else []
            i = 0
            while i < len(items):
                first = int(items[i])
                nxt = items[i + 1] if i + 1 < len(items) else None
                if isinstance(nxt, pikepdf.Array):
                    for k, v in enumerate(nxt):
                        out[first + k] = _num(v)
                    i += 2
                elif i + 2 < len(items):
                    # A CID is two bytes: a range that claims more is the file's error, not a reason to build it
                    for c in range(max(0, first), min(int(nxt), MAX_CID) + 1):
                        out[c] = _num(items[i + 2])
                    i += 3
                else:
                    break
            result = _DefaultWidths(out, default)
        else:
            first = int(font.get("/FirstChar", 0))
            ws = font.get("/Widths")
            if isinstance(ws, pikepdf.Array):
                for k, v in enumerate(ws):
                    out[first + k] = _num(v)
            result = out or None
    except Exception:  # noqa: BLE001 - odd fonts: fall back to estimates
        result = None
    if key is not None:
        cache[key] = result
    return result


class _DefaultWidths(dict):
    def __init__(self, data, default):
        super().__init__(data)
        self.default = default

    def get(self, k, d=None):  # noqa: D102
        return super().get(k, self.default)


def _split_tj(operand) -> list | None:
    """Pieces of a TJ array, split before each adjustment that moves the pen right by a large gap.
    The adjustment stays at the start of the next piece, so the pen ends up exactly where it would."""
    if not isinstance(operand, pikepdf.Array) or len(operand) < 3:
        return None
    pieces: list[list] = [[]]
    for it in operand:
        if not isinstance(it, pikepdf.String) and _num(it) <= SPLIT_GAP and any(
                isinstance(x, pikepdf.String) for x in pieces[-1]):
            pieces.append([])
        pieces[-1].append(it)
    if len(pieces) < 2:
        return None
    return [pikepdf.Array(p) for p in pieces]


def count_form_uses(pdf: pikepdf.Pdf) -> dict[tuple[int, int], int]:
    """How many times each form XObject is drawn from page content (forms are only tagged
    internally when drawn exactly once; shared forms are tagged as a single unit)."""
    counts: dict[tuple[int, int], int] = {}
    for page in pdf.pages:
        res = page.obj.get("/Resources")
        xobjs = res.get("/XObject") if res is not None else None
        if xobjs is None:
            continue
        try:
            instructions = pikepdf.parse_content_stream(page.obj)
        except pikepdf.PdfError:
            continue
        for ins in instructions:
            if isinstance(ins, ContentStreamInstruction) and str(ins.operator) == "Do" and ins.operands:
                x = xobjs.get(ins.operands[0])
                if x is not None and x.get("/Subtype") == Name.Form:
                    counts[x.objgen] = counts.get(x.objgen, 0) + 1
    return counts
