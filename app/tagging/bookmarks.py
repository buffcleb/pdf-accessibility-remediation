"""Bookmarks (the document outline) made from the headings in the tag tree.

A long document without bookmarks fails Acrobat's accessibility check and WCAG 2.4.5 (Multiple Ways, technique
PDF2). The headings already say how the document is organized, so the outline follows them: H1 to H6 nest by
level, each bookmark goes to its heading's page and the top of the heading.

An outline the document came with is kept when it works: the author may have organized it differently on
purpose. It's replaced only when there is none, when none of its entries goes anywhere, or when the engine made
it (marked with /RemediationBookmarks), so a save from the tag manager can bring it up to date with the headings.

The headings come from a tag map (app/verification/tagmap.py), which every output already has: reading the text
of a long document again just for its headings would take as long as the tag map itself.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pikepdf
import pymupdf as fitz
from pikepdf import Array, Dictionary, Name, String

from app.verification.integrity import check_integrity

MARK = Name("/RemediationBookmarks")
MIN_HEADINGS = 2      # one heading gives a bookmark panel with one line in it: no help finding anything
MAX_TITLE = 120
_LEVEL = re.compile(r"^H([1-6])$")


@dataclass
class Entry:
    level: int
    title: str
    page: int                 # 0-based
    top: float | None         # the heading's top in tag-map units (points from the top of the page as shown)
    left: float | None
    element: int | None = None  # the heading's place in the tag map (and the tree's pre-order)
    kids: list["Entry"] = field(default_factory=list)


def headings(tagmap: dict) -> list[Entry]:
    """The headings of a tag map, in tree order, with their level, words and place. A plain H (no number) takes
    the level of the heading before it, or 1."""
    out: list[Entry] = []
    level = 1
    for e in tagmap.get("elements", []):
        role = e.get("role") or ""
        m = _LEVEL.match(role)
        if not m and role != "H":
            continue
        level = int(m.group(1)) if m else level
        words = " ".join(str(e.get("actual_text") or e.get("text") or e.get("alt") or "").split())
        boxes = e.get("boxes") or []
        if not words or not boxes:
            continue  # nothing to call it, or nowhere to go
        if len(words) > MAX_TITLE:
            words = words[:MAX_TITLE - 1].rstrip() + "…"
        first = boxes[0]
        out.append(Entry(level, words, first["page"] - 1, first["bbox"][1], first["bbox"][0], e.get("i")))
    return out


def nest(flat: list[Entry]) -> list[Entry]:
    """Each heading goes under the nearest heading before it of a higher level (a smaller number). A skipped
    level (H1 then H3) just nests one deeper."""
    top: list[Entry] = []
    stack: list[Entry] = []
    for e in flat:
        e.kids = []
        while stack and stack[-1].level >= e.level:
            stack.pop()
        (stack[-1].kids if stack else top).append(e)
        stack.append(e)
    return top


def outline_state(pdf: pikepdf.Pdf) -> str:
    """"none", "ours" (made here before), "broken" (no entry goes anywhere) or "kept" (the document's own, working)."""
    outlines = pdf.Root.get("/Outlines")
    if not isinstance(outlines, Dictionary) or not isinstance(outlines.get("/First"), Dictionary):
        return "none"
    if outlines.get(MARK) is True:
        return "ours"
    pages = {p.obj.objgen for p in pdf.pages}
    seen: set = set()

    def goes_somewhere(item: Dictionary) -> bool:
        dest = item.get("/Dest")
        action = item.get("/A")
        if dest is None and isinstance(action, Dictionary):
            if action.get("/S") != Name.GoTo:
                return True  # a link to a web page or another file still goes somewhere
            dest = action.get("/D")
        if isinstance(dest, (String, Name)):
            return True  # a named destination: resolved by the viewer
        if isinstance(dest, Array) and len(dest) and isinstance(dest[0], Dictionary) and dest[0].is_indirect:
            return dest[0].objgen in pages
        return False

    stack = [outlines.get("/First")]
    while stack:
        item = stack.pop()
        if not isinstance(item, Dictionary) or (item.is_indirect and item.objgen in seen) or len(seen) > 100_000:
            continue
        if item.is_indirect:
            seen.add(item.objgen)
        if goes_somewhere(item):
            return "kept"
        stack += [item.get("/Next"), item.get("/First")]
    return "broken"


def write_bookmarks(path: Path, tagmap: dict, *, rebuild_only: bool = False) -> dict:
    """Write the outline into the file at path (in place) when it should be. With rebuild_only, only an outline made
    here before is brought up to date (after people's edits): a file without one is left as it was.

    Returns {"bookmarks": how many were written (0 when none), "outline": the state found}."""
    path = Path(path)
    flat = headings(tagmap)
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        state = outline_state(pdf)
        if state == "kept" or (rebuild_only and state != "ours"):
            return {"bookmarks": 0, "outline": state}
        if len(flat) < MIN_HEADINGS:
            if state == "ours":  # the headings it came from are gone
                del pdf.Root["/Outlines"]
                pdf.save(path)
            return {"bookmarks": 0, "outline": state}
        tops = _pdf_tops(path, flat)
        _, index = check_integrity(pdf)
        tags = {e.index: e.obj for e in index.elements if e.obj.is_indirect}
        root = pdf.make_indirect(Dictionary(Type=Name.Outlines))
        root[MARK] = True
        _link(pdf, root, nest(flat), tops, tags)
        pdf.Root.Outlines = root
        pdf.save(path)
    return {"bookmarks": len(flat), "outline": state}


def _pdf_tops(path: Path, flat: list[Entry]) -> dict[int, tuple[float | None, float | None]]:
    """Each heading's left and top in the page's own coordinates (PDF space, origin bottom left), from the tag
    map's (origin top left)."""
    out: dict[int, tuple[float | None, float | None]] = {}
    with fitz.open(path) as doc:
        for e in flat:
            if e.top is None or not 0 <= e.page < len(doc) or doc[e.page].rotation:
                continue  # on a turned page "top" isn't up the page: the bookmark just opens the page
            page = doc[e.page]
            p = fitz.Point(e.left or 0, e.top) * ~page.transformation_matrix
            out[id(e)] = (round(p.x, 2), round(p.y + 2, 2))  # a little above, so the heading isn't cut off
    return out


def _link(pdf: pikepdf.Pdf, parent: Dictionary, entries: list[Entry], tops: dict, tags: dict) -> int:
    """Make the outline items for entries under parent, closed (a long thesis opens as its chapters), and return
    how many items show when parent is open.

    Each item goes to its heading two ways: the page and position (/D), which every reader follows, and the heading's
    tag (/SD, a structure destination), which PDF/UA-2 requires of destinations inside the document."""
    items = []
    for e in entries:
        left, top = tops.get(id(e), (None, None))
        page = pdf.pages[e.page].obj
        go = Dictionary(S=Name.GoTo, D=Array([page, Name.XYZ, left, top, None]))  # None (null): keep the zoom
        tag = tags.get(e.element)
        if tag is not None:
            go.SD = Array([tag, Name.XYZ, left, top, None])
        items.append(pdf.make_indirect(Dictionary(Title=String(e.title), Parent=parent, A=go)))
    for i, (e, item) in enumerate(zip(entries, items)):
        if i:
            item.Prev = items[i - 1]
        if i + 1 < len(items):
            item.Next = items[i + 1]
        if e.kids:
            _link(pdf, item, e.kids, tops, tags)
            item.Count = -len(e.kids)  # closed: negative, the number of items opening it would show
    parent.First, parent.Last = items[0], items[-1]
    parent.Count = len(items)
    return len(items)
