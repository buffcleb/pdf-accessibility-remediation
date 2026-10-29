"""A part of a picture as a tag of its own: a box drawn over a picture the page draws (a poster's artwork, a scan) is
drawn again, clipped to the box, right after the picture, and that copy is what the new tag marks. The same image
is drawn with the same matrix over the same pixels, so the page looks exactly the same; the picture itself stays as
it was (usually a background artifact). This is how the scanned-page figures are made (app/pipeline/scan_figures),
placed where nothing drawn after the picture can be covered by the copy.

A see-through picture (a soft mask: a poster's artwork with shadows and smooth edges) would darken where it's partly
transparent if drawn twice, so its copy is drawn fully transparent (an ExtGState with opacity 0): it still gives the
tag its content and its place on the page, and nothing on the page changes. What can't be copied is refused rather
than guessed: a picture turned or skewed, one clipped to a shape that isn't a rectangle, or one drawn inside a form
XObject."""
from __future__ import annotations

import pikepdf
import pymupdf as fitz
from pikepdf import ContentStreamInstruction, Dictionary, Name, Operator

from app.tagging.content_marker import apply as apply_matrix
from app.tagging.content_marker import existing_mcids, mat_mul
from app.tagging.editor._ctx import EditError

Matrix = tuple[float, float, float, float, float, float]
_IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
# What draws: anything between the picture and the place its copy goes would be covered by the copy
_PAINT = {"Tj", "TJ", "'", '"', "S", "s", "f", "F", "f*", "B", "B*", "b", "b*", "sh", "Do", "BI", "EI"}


def _axis_aligned(m: Matrix) -> bool:
    return abs(m[1]) < 1e-9 and abs(m[2]) < 1e-9 and abs(m[0]) > 1e-9 and abs(m[3]) > 1e-9


def _invert(m: Matrix) -> Matrix:
    a, b, c, d, e, f = m
    det = a * d - b * c
    return (d / det, -b / det, -c / det, a / det, (c * f - d * e) / det, (b * e - a * f) / det)


def _rect(m: Matrix, x0: float, y0: float, x1: float, y1: float) -> tuple[float, float, float, float]:
    pts = [apply_matrix(m, x, y) for x, y in ((x0, y0), (x1, y0), (x0, y1), (x1, y1))]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _meet(a, b):
    if a is None:
        return b
    r = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
    return r if r[0] < r[2] and r[1] < r[3] else (0.0, 0.0, 0.0, 0.0)


def _draws(ops: list, images: set[str]) -> list[dict]:
    """Each top-level draw of an image XObject: where it is (PDF user space), the rectangle it's clipped to (None:
    none; "shape": a clip that isn't a rectangle), its matrix, and where its q..Q group and marked sections close."""
    ctm, clip, stack = _IDENTITY, None, []
    marked = 0
    path: list = []  # the rectangles of the path being built (None for anything else)
    found, waiting = [], []
    after: list[Matrix] = []  # the ctm at each instruction's end
    for i, ins in enumerate(ops):
        if not isinstance(ins, ContentStreamInstruction):
            after.append(ctm)
            continue
        op, args = str(ins.operator), list(ins.operands)
        if op == "q":
            stack.append((ctm, clip))
        elif op == "Q" and stack:
            ctm, clip = stack.pop()
        elif op == "cm" and len(args) == 6:
            ctm = mat_mul(tuple(float(v) for v in args), ctm)
        elif op == "re" and len(args) == 4:
            x, y, w, h = (float(v) for v in args)
            path.append(_rect(ctm, x, y, x + w, y + h) if _axis_aligned(ctm) else None)
        elif op in ("m", "l", "c", "v", "y", "h"):
            path.append(None)
        elif op in ("W", "W*"):
            r = path[0] if len(path) == 1 else None
            clip = "shape" if r is None or clip == "shape" else _meet(clip, r)
        elif op in ("n", "S", "s", "f", "F", "f*", "B", "B*", "b", "b*"):
            path = []
        elif op in ("BDC", "BMC"):
            marked += 1
        elif op == "EMC":
            marked = max(0, marked - 1)
        elif op == "Do" and args and str(args[0]) in images:
            found.append({"at": i, "name": args[0], "m": ctm, "clip": clip, "where": _rect(ctm, 0, 0, 1, 1)})
            waiting.append(found[-1])
        after.append(ctm)
        # Its copy goes once the marked section it's drawn in (an artifact, a figure) is closed: right after it,
        # inside whatever q..Q it's in (a page's content can be one q..Q to its end, the scan copies' doing), with
        # the matrix there; the clip there is the picture's own, so the copy shows no more than it did
        if not marked:
            for d in waiting:
                d["end"], d["ctm_after"] = i, ctm
            waiting = []
    return found


def redraw_part(ctx, page_no: int, box: list[float], role: str, n: int) -> int:
    """Draw the part of the picture under box (the tag map's page units) again, as marked content of role with a new
    MCID, which is returned for the tag that is to hold it."""
    page = ctx.pdf.pages[page_no - 1]
    with fitz.open(ctx.path) as doc:
        fp = doc[page_no - 1]
        to_pdf = tuple(~fp.transformation_matrix)
    want = _rect(to_pdf, *box)
    resources = page.obj.get("/Resources") or Dictionary()
    xobjects = resources.get("/XObject") or Dictionary()
    images = {str(k): v for k, v in xobjects.items() if isinstance(v, pikepdf.Stream) and v.get("/Subtype") == Name.Image}
    ops = list(pikepdf.parse_content_stream(page.obj))
    # The draw that shows the most of the box: the picture itself, not a copy made for an earlier figure (clipped to
    # that figure's box, it would cut this one down to a sliver); of two that show as much, the later, on top
    target, best = None, 0.0
    for d in _draws(ops, set(images)):
        r = _meet(d["where"] if d["clip"] in (None, "shape") else _meet(d["where"], d["clip"]), want)
        if r[2] - r[0] < 1 or r[3] - r[1] < 1:
            continue
        size = (r[2] - r[0]) * (r[3] - r[1])
        if size >= best:
            target, best = (d, r), size
    if target is None:
        raise EditError(f"Edit {n}: there's no picture under that box on page {page_no} to make a part of it a "
                        "figure.")
    d, r = target
    img = images[str(d["name"])]
    if img.get("/ImageMask") is True:
        raise EditError(f"Edit {n}: what's under that box is a stencil (a shape filled with a colour), not a picture.")
    see_through = "/SMask" in img or "/Mask" in img
    if d["clip"] == "shape" or not _axis_aligned(d["m"]):
        raise EditError(f"Edit {n}: the picture under that box is turned or cut to a shape, so a part of it can't be "
                        "made a figure of its own.")
    if "end" not in d:
        raise EditError(f"Edit {n}: the picture under that box isn't drawn in a way a part of it can be copied.")
    if any(isinstance(ins, ContentStreamInstruction) and str(ins.operator) in _PAINT
           for ins in ops[d["at"] + 1:d["end"] + 1]):
        raise EditError(f"Edit {n}: something is drawn right after the picture under that box, which a copy of it "
                        "would cover.")
    here = d["ctm_after"]
    if not _axis_aligned(here):
        raise EditError(f"Edit {n}: the page is drawn turned at that point, so a part of the picture can't be copied.")
    back = _invert(here)
    x0, y0, x1, y1 = _rect(back, *r)
    m = mat_mul(d["m"], back)
    mcid = max(existing_mcids(ops) or {-1}) + 1
    num = lambda v: f"{v:.6f}".rstrip("0").rstrip(".")  # noqa: E731
    unseen = []
    if see_through:
        states = resources.get("/ExtGState")
        if not isinstance(states, Dictionary):
            states = resources.ExtGState = Dictionary()
            page.obj.Resources = resources
        name = next((k for k, v in states.items() if isinstance(v, Dictionary) and v.get("/RemediationUnseen")), None)
        if name is None:
            name = "/RemediationUnseen"
            states[name] = ctx.pdf.make_indirect(Dictionary(Type=Name.ExtGState, ca=0, CA=0, RemediationUnseen=True))
        unseen = [ContentStreamInstruction([Name(name)], Operator("gs"))]
    copy = [
        ContentStreamInstruction([], Operator("q")),
        *unseen,
        ContentStreamInstruction([float(num(x0)), float(num(y0)), float(num(x1 - x0)), float(num(y1 - y0))], Operator("re")),
        ContentStreamInstruction([], Operator("W")),
        ContentStreamInstruction([], Operator("n")),
        ContentStreamInstruction([float(num(v)) for v in m], Operator("cm")),
        ContentStreamInstruction([Name("/" + role), Dictionary(MCID=mcid)], Operator("BDC")),
        ContentStreamInstruction([d["name"]], Operator("Do")),
        ContentStreamInstruction([], Operator("EMC")),
        ContentStreamInstruction([], Operator("Q")),
    ]
    ops[d["end"] + 1:d["end"] + 1] = copy
    page.obj.Contents = ctx.pdf.make_stream(pikepdf.unparse_content_stream(ops))
    return mcid
