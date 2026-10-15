"""Apply people's tag edits from the portal's tag manager.

Edits change the structure tree only. Content streams are untouched, so pages look exactly the same.
The one exception is "decorative", which relabels the element's marked content as an artifact; that
changes no drawing operators either.

Each operation names an element by its index in the source file's tag map (the pre-order index the
verifier and the tag map use) and the role it expects there. Every element is resolved to its PDF
object before any edit is applied, so moves can't shift the targets of later operations.

Operations:
    {"op": "set_role", "element": 12, "expect_role": "P", "role": "H2"}
    {"op": "set_alt", "element": 30, "expect_role": "Figure", "alt": "Bar chart of ..."}   ("" removes it)
    {"op": "set_actual_text", "element": 31, "expect_role": "Formula", "text": "x squared"}
    {"op": "set_decorative", "element": 30, "expect_role": "Figure"}
    {"op": "set_lang", "element": 40, "expect_role": "P", "lang": "fr-CA"}                ("" removes it)
    {"op": "set_cell", "element": 77, "expect_role": "TD", "role": "TH", "scope": "Column"}
    {"op": "move", "element": 15, "expect_role": "P", "target": 9, "position": "before"}
    {"op": "merge", "element": 20, "expect_role": "P", "absorb": [21, 22]}   (21 and 22 join 20, in that order)
    {"op": "split", "element": 30, "expect_role": "P", "at": 2}   (the /K items from position 2 become a new tag)
    {"op": "split", "element": 30, "expect_role": "P", "at": 0, "char": 14}
        (split inside text: /K item 0 is split before character 14 of its text, as the tag map shows it,
        and that remainder plus any later items become a new tag. The content stream is split at that
        glyph, which draws exactly the same.)
    {"op": "delete", "element": 12, "expect_role": "Sect", "children": "lift"}
        (remove the tag and keep what's inside: its child tags move up a level, where that's valid. Content it
        holds directly joins the parent when the parent holds content (a Span in a paragraph); otherwise it
        becomes an artifact)
    {"op": "delete", "element": 12, "expect_role": "P", "children": "delete"}
        (delete the tag and everything in it: all its content becomes artifacts, which aren't read)
    {"op": "assign", "element": 12, "expect_role": "P", "pieces": [{"page": 2, "mcid": 7}, ...]}
        (change the area a tag covers: those pieces of tagged content, from the tag map's "pieces", move from the
        tags holding them into this one; a tag left with nothing is removed)
    {"op": "tag_artifacts", "element": 12, "expect_role": "P", "position": "after", "page": 2,
     "sections": [3, 4], "role": "Figure", "alt": "..."}
        (tag a new area: those artifact sections of the page's stream, numbered as the tag map's "artifacts",
        become one new tag of that role, placed before or after element 12. Only the marking changes.)
    {"op": "make_list", "element": 20, "expect_role": "P", "absorb": [21, 22]}
        (20, 21 and 22 become the items of one list, in that order, where 20 was: each text tag becomes
        LI > LBody holding its content; a list among them gives its items)
    {"op": "indent", "element": 21, "expect_role": "P"}
        (a list item goes one level deeper: into a list nested at the end of the item before it)
    {"op": "outdent", "element": 21, "expect_role": "P"}
        (an item of a nested list goes one level up, right after the item it was under; the items after it
        in the nested list go under it, so the reading order stays the same)
    {"op": "build_list", "element": 17, "expect_role": "P", "absorb": [18],
     "rows": [{"at": 0, "as": "H3"}, {"at": 3, "level": 1}, {"at": 5, "level": 2}, {"el": 18, "at": 0, "level": 1}]}
        (a tag's lines become a heading, a list and its nested lists in one go: the tag's /K items, followed by
        those of the tags in "absorb", are cut into rows. A row starts at /K item "at" of tag "el" (the tag itself
        when left out) and runs to the next row. A row with "as" becomes a tag of that type; rows with "level"
        become the items of a list, an item one level deeper than the one before it going into a list nested
        under that one. The results take the tag's place, in order.)
    {"op": "take_pieces", "element": 30, "expect_role": "Figure", "pieces": [{"page": 2, "mcid": 7}, ...],
     "role": "Figure", "alt": "..."}
        (some of a tag's pieces of content, from the tag map's "pieces", become a new tag of that role, read right
        after it: one drawing out of a figure that holds several. Some content has to stay in the tag.)
    {"op": "cut_area", "element": 30, "expect_role": "Figure", "page": 2, "bbox": [x0, y0, x1, y1], "role": "Figure",
     "alt": "..."}
        (what of a tag's content on that page lies inside a box, in the tag map's page units, becomes a new tag of
        that role, read right after it: one structure out of a scheme drawn as a single piece. Each drawing or text
        instruction goes with the side of the box that holds its middle; the page's marked sections are closed and
        reopened between them, never reordered, so the page draws the same.)
    {"op": "set_headers", "element": 77, "expect_role": "TD", "headers": [70, 74]}
        (the header cells a table cell is read with, in that order: Headers on the cell naming their IDs, which they
        are given if they have none. [] removes them. The headers must be header cells of the same table.)
    {"op": "link_note", "element": 18, "expect_role": "Reference", "note": 52}
        (the tag points at a note: /Ref names the note, which is given an ID if it has none; "note": null removes what
        the tag points at. For a footnote the rules didn't link: the mark, or the paragraph it's in, and its note.)
    {"op": "make_toc", "element": 40, "expect_role": "L"}
        (a list becomes a table of contents: L -> TOC, each LI -> TOCI keeping its label, the item's text -> a
        Reference in the entry (a link in it stays inside), and a list nested in an item -> a TOC in that entry)
    take_pieces and cut_area look at what the tag is when its turn comes, so they can follow a make_list in the same
    save; expect_role is still what the tag was in the file.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pikepdf
from pikepdf import Array, ContentStreamInstruction, Dictionary, Name, Operator, String

from app.tagging.content_marker import mcid_of
from app.tagging.role_rules import BLOCKS, check_move, check_role
from app.tagging.table_headers import headers_named, id_of, table_attrs, write_id_tree
from app.verification.integrity import STANDARD, Elem, IntegrityChecker, _items

OPS = {"set_role", "set_alt", "set_actual_text", "set_decorative", "set_lang", "set_cell", "move", "merge", "split",
       "assign", "tag_artifacts", "delete", "make_list", "indent", "outdent", "build_list", "take_pieces", "cut_area",
       "make_toc", "set_headers", "link_note"}
MAX_HEADERS = 50
# What a row of build_list can become apart from a list item
ROW_ROLES = {"P", "H1", "H2", "H3", "H4", "H5", "H6"}
MAX_ROWS = 500
MAX_LIST_LEVEL = 9
# Text tags that can become a list item as they are. A figure or formula would lose its alt text, and the inline
# ones (Span, Quote, Reference) belong to the text around them
LIST_ITEM_FROM = {"H", "H1", "H2", "H3", "H4", "H5", "H6", "P", "Caption", "BlockQuote", "Note", "Code", "BibEntry"}
# Tags that hold content of their own (not only other tags): the ones a piece of content can be given to
CONTENT_ROLES = {"H", "H1", "H2", "H3", "H4", "H5", "H6", "P", "Caption", "BlockQuote", "Note", "Code", "Quote", "Span",
                 "Reference", "BibEntry", "Figure", "Formula", "TD", "TH", "LBody", "Lbl", "Link", "Form", "TOCI"}
# Tags a new area can become (a cell, list part or link needs its structure, so isn't made from artifacts)
NEW_AREA_ROLES = {"H1", "H2", "H3", "H4", "H5", "H6", "P", "Caption", "BlockQuote", "Note", "Code", "Quote",
                  "Figure", "Formula"}
SCOPES = {"Row", "Column", "Both"}
MAX_OPS = 5000
MAX_TEXT = 4000
_LANG = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*$")
_MARK = Name("/RemediationEditMark")


class EditError(ValueError):
    """An edit can't be applied; the message is safe to show to the person who made it."""


@dataclass
class _Ctx:
    pdf: pikepdf.Pdf
    root: Dictionary
    checker: IntegrityChecker
    elems: list[Elem]
    path: Path | None = None
    # Artifact sections already tagged by earlier edits, per page (numbered as in the tag map)
    tagged_sections: dict | None = None
    ids_changed: bool = False  # an element ID was given or taken away: the IDTree is rebuilt before saving


def validate_ops(ops) -> list[dict]:
    """Shape checks that don't need the PDF (the portal runs these before queueing)."""
    if not isinstance(ops, list) or not ops:
        raise EditError("No edits to apply.")
    if len(ops) > MAX_OPS:
        raise EditError(f"Too many edits at once (at most {MAX_OPS}).")
    clean = []
    for n, op in enumerate(ops, 1):
        if not isinstance(op, dict) or op.get("op") not in OPS:
            raise EditError(f"Edit {n}: unknown operation.")
        if not isinstance(op.get("element"), int) or op["element"] < 0:
            raise EditError(f"Edit {n}: missing element.")
        kind = op["op"]
        if kind == "set_role" and op.get("role") not in STANDARD:
            raise EditError(f"Edit {n}: {op.get('role')!r} isn't a standard PDF tag.")
        if kind in ("set_alt", "set_actual_text"):
            text = op.get("alt" if kind == "set_alt" else "text")
            if not isinstance(text, str) or len(text) > MAX_TEXT:
                raise EditError(f"Edit {n}: text must be at most {MAX_TEXT} characters.")
        if kind == "set_lang" and op.get("lang") and not _LANG.match(str(op["lang"])):
            raise EditError(f"Edit {n}: {op.get('lang')!r} isn't a language code like en-US.")
        if kind == "link_note":
            note = op.get("note")
            if note is not None and (not isinstance(note, int) or isinstance(note, bool) or note < 0 or note == op["element"]):
                raise EditError(f"Edit {n}: a link needs the note to point at (or null to remove it).")
        if kind == "set_headers":
            heads = op.get("headers")
            if (not isinstance(heads, list) or len(heads) > MAX_HEADERS or len(set(heads)) != len(heads)
                    or not all(isinstance(h, int) and not isinstance(h, bool) and h >= 0 for h in heads)
                    or op["element"] in heads):
                raise EditError(f"Edit {n}: a cell's headers are a list of other cells (at most {MAX_HEADERS}).")
        if kind == "set_cell":
            if op.get("role") not in ("TH", "TD"):
                raise EditError(f"Edit {n}: a cell must be TH or TD.")
            if op["role"] == "TH" and op.get("scope") not in SCOPES:
                raise EditError(f"Edit {n}: a header cell needs a scope of Row, Column or Both.")
        if kind == "move":
            if not isinstance(op.get("target"), int) or op.get("position") not in ("before", "after"):
                raise EditError(f"Edit {n}: a move needs a target element and before/after.")
            if op["target"] == op["element"]:
                raise EditError(f"Edit {n}: an element can't be moved next to itself.")
        if kind in ("merge", "make_list"):
            absorb = op.get("absorb")
            if (not isinstance(absorb, list) or not absorb or len(absorb) > 500
                    or not all(isinstance(a, int) and a >= 0 for a in absorb) or op["element"] in absorb
                    or len(set(absorb)) != len(absorb)):
                raise EditError(f"Edit {n}: a merge needs a list of other tags to join this one." if kind == "merge"
                                else f"Edit {n}: making a list needs the other tags that become its items.")
        if kind == "build_list":
            absorb, rows = op.get("absorb") or [], op.get("rows")
            if (not isinstance(absorb, list) or len(absorb) > 500 or op["element"] in absorb or len(set(absorb)) != len(absorb)
                    or not all(isinstance(a, int) and not isinstance(a, bool) and a >= 0 for a in absorb)):
                raise EditError(f"Edit {n}: building a list needs the other tags whose lines it takes.")
            if not isinstance(rows, list) or not rows or len(rows) > MAX_ROWS:
                raise EditError(f"Edit {n}: building a list needs its rows (at most {MAX_ROWS}).")
            for row in rows:
                whole = lambda v: isinstance(v, int) and not isinstance(v, bool)  # noqa: E731
                if (not isinstance(row, dict) or not whole(row.get("at")) or row["at"] < 0
                        or ("el" in row and row["el"] != op["element"] and row["el"] not in absorb)
                        or ("level" in row) == ("as" in row)
                        or ("level" in row and not (whole(row["level"]) and 1 <= row["level"] <= MAX_LIST_LEVEL))
                        or ("as" in row and row["as"] not in ROW_ROLES)):
                    raise EditError(f"Edit {n}: each row of a list being built needs where it starts and either a "
                                    f"list level (1 to {MAX_LIST_LEVEL}) or a type ({', '.join(sorted(ROW_ROLES))}).")
        if kind == "delete" and op.get("children") not in ("lift", "delete"):
            raise EditError(f"Edit {n}: deleting a tag needs to say what happens to what's inside it: "
                            "\"lift\" (keep it, a level up) or \"delete\" (remove it all).")
        if kind in ("assign", "take_pieces"):
            pieces = op.get("pieces")
            if (not isinstance(pieces, list) or not pieces or len(pieces) > 2000
                    or not all(isinstance(x, dict) and isinstance(x.get("page"), int) and x["page"] >= 1
                               and isinstance(x.get("mcid"), int) and x["mcid"] >= 0 for x in pieces)):
                raise EditError(f"Edit {n}: changing a tag's area needs the pieces of content to give it." if kind == "assign"
                                else f"Edit {n}: making a tag from pieces of another needs the pieces.")
        if kind == "cut_area":
            box = op.get("bbox")
            if (not isinstance(op.get("page"), int) or op["page"] < 1 or not isinstance(box, list) or len(box) != 4
                    or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in box)
                    or box[2] - box[0] < 1 or box[3] - box[1] < 1):
                raise EditError(f"Edit {n}: cutting an area needs the page and a box (left, top, right, bottom).")
        if kind in ("take_pieces", "cut_area"):
            if op.get("role") not in NEW_AREA_ROLES:
                raise EditError(f"Edit {n}: the new tag can be {', '.join(sorted(NEW_AREA_ROLES))}.")
            if op["role"] in ("Figure", "Formula") and not str(op.get("alt") or "").strip():
                raise EditError(f"Edit {n}: a {op['role'].lower()} needs alt text describing it.")
            if len(str(op.get("alt") or "")) > MAX_TEXT:
                raise EditError(f"Edit {n}: text must be at most {MAX_TEXT} characters.")
        if kind == "tag_artifacts":
            sections = op.get("sections")
            if (not isinstance(op.get("page"), int) or op["page"] < 1 or not isinstance(sections, list) or not sections
                    or len(sections) > 500 or not all(isinstance(x, int) and x >= 0 for x in sections)
                    or op.get("position") not in ("before", "after")):
                raise EditError(f"Edit {n}: tagging an area needs the page, the sections and where the new tag goes.")
            if op.get("role") not in NEW_AREA_ROLES:
                raise EditError(f"Edit {n}: a new area can be tagged as {', '.join(sorted(NEW_AREA_ROLES))}.")
            if op["role"] in ("Figure", "Formula") and not str(op.get("alt") or "").strip():
                raise EditError(f"Edit {n}: a {op['role'].lower()} needs alt text describing it.")
            if len(str(op.get("alt") or "")) > MAX_TEXT:
                raise EditError(f"Edit {n}: text must be at most {MAX_TEXT} characters.")
        if kind == "split":
            inside = op.get("char") is not None
            if (not isinstance(op.get("at"), int) or op["at"] < (0 if inside else 1)
                    or (inside and (not isinstance(op["char"], int) or isinstance(op["char"], bool) or op["char"] < 1))):
                raise EditError(f"Edit {n}: a split needs the position to split at.")
        clean.append(op)
    return clean


def apply_edits(src: Path, dst: Path, ops: list[dict]) -> list[str]:
    """Apply the edits to src and save dst. Returns one plain-language line per edit."""
    ops = validate_ops(ops)
    with pikepdf.open(src) as pdf:
        root = pdf.Root.get("/StructTreeRoot")
        if not isinstance(root, Dictionary):
            raise EditError("This file has no tags to edit.")
        checker = IntegrityChecker(pdf)
        checker.run()
        ctx = _Ctx(pdf, root, checker, checker.index.elements, Path(src))
        # Resolve every reference first; later moves change pre-order indices but not objects.
        resolved = []
        for n, op in enumerate(ops, 1):
            e = _elem(ctx, op["element"], op.get("expect_role"), n)
            if op["op"] == "move":
                t = _elem(ctx, op["target"], op.get("expect_target_role"), n)
            elif op["op"] in ("merge", "make_list", "build_list"):
                t = [_elem(ctx, a, None, n) for a in op.get("absorb") or []]
            elif op["op"] == "set_headers":
                t = [_elem(ctx, h, None, n) for h in op["headers"]]
            elif op["op"] == "link_note" and op.get("note") is not None:
                t = _elem(ctx, op["note"], None, n)
            else:
                t = None
            resolved.append((op, e, t))
        log = []
        for n, (op, e, t) in enumerate(resolved, 1):
            log.append(_apply(ctx, op, e, t, n))
        if ctx.ids_changed:
            write_id_tree(pdf, root, _walk(root))
        pdf.save(dst)
    return log


def _elem(ctx: _Ctx, index: int, expect_role: str | None, n: int) -> Elem:
    if index >= len(ctx.elems):
        raise EditError(f"Edit {n}: element {index} doesn't exist in this version. Reload and try again.")
    e = ctx.elems[index]
    if expect_role and expect_role not in (e.role, e.raw_role):
        raise EditError(f"Edit {n}: element {index} is a {e.role}, not a {expect_role}. "
                        "The document changed; reload and try again.")
    return e


def _apply(ctx: _Ctx, op: dict, e: Elem, t: Elem | None, n: int) -> str:
    kind, obj = op["op"], e.obj
    if kind == "set_role":
        return _set_role(ctx, e, op["role"], n)
    if kind == "set_alt":
        return _set_text(obj, "/Alt", op["alt"], e, "alt text")
    if kind == "set_actual_text":
        return _set_text(obj, "/ActualText", op["text"], e, "actual text")
    if kind == "set_lang":
        lang = (op.get("lang") or "").strip()
        if lang:
            obj.Lang = String(lang)
        elif "/Lang" in obj:
            del obj["/Lang"]
        return f"element {e.index}: language {'set to ' + lang if lang else 'removed'}"
    if kind == "set_cell":
        return _set_cell(ctx, e, op["role"], op.get("scope"))
    if kind == "set_headers":
        return _set_headers(ctx, e, t, n)
    if kind == "link_note":
        return _link_note(ctx, e, t, n)
    if kind == "set_decorative":
        return _make_decorative(ctx, e, n)
    if kind == "move":
        return _move(ctx, e, t, op["position"], n)
    if kind == "merge":
        return _merge(ctx, e, t, n)
    if kind == "split":
        if op.get("char") is not None:
            return _split_inside(ctx, e, op["at"], op["char"], n)
        return _split(ctx, e, op["at"], n)
    if kind == "delete":
        return _delete(ctx, e, op["children"], n)
    if kind == "assign":
        return _assign(ctx, e, op["pieces"], n)
    if kind == "take_pieces":
        return _take_pieces(ctx, e, op, n)
    if kind == "cut_area":
        return _cut_area(ctx, e, op, n)
    if kind == "tag_artifacts":
        return _tag_artifacts(ctx, e, op, n)
    if kind == "make_list":
        return _make_list(ctx, e, t, n)
    if kind == "build_list":
        return _build_list(ctx, e, t, op["rows"], n)
    if kind == "indent":
        return _indent(ctx, e, n)
    if kind == "outdent":
        return _outdent(ctx, e, n)
    if kind == "make_toc":
        return _make_toc(ctx, e, n)
    raise EditError(f"Edit {n}: unknown operation.")  # validate_ops prevents this


def _set_role(ctx: _Ctx, e: Elem, role: str, n: int) -> str:
    """Change a tag's type, reshaping the structure around it when the new type needs that (see
    role_rules), or refuse a change that can't make a valid tree."""
    parent = ctx.elems[e.parent].role if e.parent is not None else None
    verdict = check_role(role, e.role, parent, [ctx.elems[k].role for k in e.kids],
                         has_content=bool(e.content_refs or e.objrs))
    if not verdict.ok:
        raise EditError(f"Edit {n}: {verdict.reason}")
    old = e.role
    if verdict.action == "collapse_list":
        made = _collapse_list(ctx, e, role, n)
        return f"element {e.index}: list -> {made} {role} tag(s), one per item"
    if verdict.action in ("wrap_list", "wrap_lbody"):
        _wrap(ctx, e, role, n)
        return f"element {e.index}: {old} -> {role} (content wrapped in {'LI > LBody' if role == 'L' else 'LBody'})"
    e.obj.S = Name("/" + role)
    if role == "Note" and e.obj.is_indirect:  # PDF/UA: a note has an ID
        _give_id(ctx, e.obj, f"note-{e.obj.objgen[0]}")
    return f"element {e.index}: {old} -> {role}"


def _kids_of(ctx: _Ctx, e: Elem) -> dict[int, Elem]:
    """Child elements by their position-free identity, to find a /K item's Elem."""
    return {ctx.elems[k].obj.objgen: ctx.elems[k] for k in e.kids if ctx.elems[k].obj.is_indirect}


def _collapse_list(ctx: _Ctx, e: Elem, role: str, n: int) -> int:
    """L -> text: each LI becomes a `role` tag holding its Lbl and LBody content (e itself becomes the
    first); lists and other blocks nested in an item, and the list's caption, follow as tags of their own."""
    def child_of(owner: Elem, item):
        if isinstance(item, Dictionary) and item.is_indirect:
            return _kids_of(ctx, owner).get(item.objgen)
        return None

    seq: list[tuple[str, list]] = []  # ("text", [(owner, item)]) or ("block", [item])
    loose: list = []  # content sitting directly in the L
    for item in _items(e.obj.get("/K")):
        child = child_of(e, item)
        if child is None:
            loose.append((e, item))
            continue
        if child.role != "LI":
            seq.append(("block", [item]))
            continue
        text, after = [], []
        for sub in _items(child.obj.get("/K")):
            part = child_of(child, sub)
            if part is not None and part.role in ("Lbl", "LBody"):
                for x in _items(part.obj.get("/K")):
                    xe = child_of(part, x)
                    if xe is not None and xe.role in BLOCKS:
                        after.append(x)
                    else:
                        text.append((part, x))
            elif part is not None and part.role in BLOCKS:
                after.append(sub)
            else:
                text.append((child, sub))
        if text:
            seq.append(("text", text))
        seq += [("block", [b]) for b in after]
    if loose:
        seq.insert(0, ("text", loose))
    if not any(kind == "text" for kind, _ in seq):
        raise EditError(f"Edit {n}: this list has no text of its own to keep as a {role}.")
    parent = _parent_obj(ctx, e)
    made: list[Dictionary] = []
    first = True
    for kind, items in seq:
        if kind == "block":
            items[0].P = parent
            made.append(items[0])
            continue
        target = e.obj if first else ctx.pdf.make_indirect(
            Dictionary(Type=Name.StructElem, S=Name("/" + role), P=parent))
        if not first and "/Pg" in e.obj:
            target.Pg = e.obj.Pg
        k: list = []
        for owner, x in items:
            k += _reparent_items(ctx, owner, [x], target)
        target.K = Array(k)
        if not first:  # the list's own tag becomes the first item's; a caption before it moves after it
            made.append(target)
        first = False
    e.obj.S = Name("/" + role)
    if "/A" in e.obj:
        del e.obj["/A"]  # the list attributes (ListNumbering) don't apply to text
    _insert_after(ctx, e, made, n)
    return 1 + sum(1 for m in made if m.get("/S") == Name("/" + role))


def _wrap(ctx: _Ctx, e: Elem, role: str, n: int) -> None:
    """Text -> L (L > LI > LBody) or, inside a list, text -> LI (LI > LBody), keeping the content."""
    body = ctx.pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.LBody))
    if "/Pg" in e.obj:
        body.Pg = e.obj.Pg
    body.K = Array(_reparent_items(ctx, e, _items(e.obj.get("/K")), body))
    if "/ActualText" in e.obj:  # it describes the text, which now sits in the LBody
        body.ActualText = e.obj.ActualText
        del e.obj["/ActualText"]
    if role == "L":
        li = ctx.pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.LI, P=e.obj, K=Array([body])))
        body.P = li
        e.obj.K = Array([li])
        e.obj.A = Dictionary(O=Name.List, ListNumbering=Name("/None"))
    else:
        body.P = e.obj
        e.obj.K = Array([body])
    e.obj.S = Name("/" + role)


def _insert_after(ctx: _Ctx, e: Elem, new: list, n: int) -> None:
    if not new:
        return
    parent = _parent_obj(ctx, e)
    if not e.obj.is_indirect:
        e.obj[_MARK] = True
    siblings = _items(parent.get("/K"))
    pos = next((i for i, k in enumerate(siblings) if _same(k, e.obj)), None)
    if _MARK in e.obj:
        del e.obj[_MARK]
    if pos is None:
        raise EditError(f"Edit {n}: the tag wasn't found under its parent.")
    parent.K = Array(siblings[:pos + 1] + new + siblings[pos + 1:])


def _set_text(obj: Dictionary, key: str, text: str, e: Elem, label: str) -> str:
    text = text.strip()
    if text:
        obj[key] = String(text)
        return f"element {e.index} ({e.role}): {label} set"
    if key in obj:
        del obj[key]
    return f"element {e.index} ({e.role}): {label} removed"


def _set_cell(ctx: _Ctx, e: Elem, role: str, scope: str | None) -> str:
    """Header or data cell. Only Scope changes: the cell's spans and the headers it names stay, or its row would no
    longer cover the same columns as the others. In a table whose cells name their headers, the names follow: a header
    made a data cell is taken out of every cell's headers, and a cell made a header is added to the cells it heads."""
    obj = e.obj
    was = _role_of(ctx, obj)
    obj.S = Name("/" + role)
    attrs = obj.get("/A")
    items = list(attrs) if isinstance(attrs, Array) else ([attrs] if attrs is not None else [])
    table = next((a for a in items if isinstance(a, Dictionary) and a.get("/O") == Name.Table), None)
    if role == "TH":
        if table is None:
            table = Dictionary(O=Name.Table)
            items.append(table)
        table.Scope = Name("/" + scope)
    elif table is not None and "/Scope" in table:
        del table["/Scope"]
        if set(table.keys()) == {"/O"}:  # nothing left but the owner
            at = next(i for i, a in enumerate(items) if a is table)
            del items[at:at + 2 if at + 1 < len(items) and isinstance(items[at + 1], int) else at + 1]
    if not items:
        if "/A" in obj:
            del obj["/A"]
    else:
        obj.A = items[0] if len(items) == 1 else Array(items)
    relinked = _relink(ctx, obj, was, role, scope) if obj.is_indirect else 0
    return (f"element {e.index}: {role}" + (f" (scope {scope})" if role == "TH" else "")
            + (f"; the headers of {relinked} cell(s) follow" if relinked else ""))


# --- the headers a cell names (Headers and ID) ------------------------------------------------------------

def _walk(root) -> list:
    out, stack, seen = [], list(reversed(_items(root.get("/K")))), set()
    while stack:
        it = stack.pop()
        if not _is_elem_dict(it) or (it.is_indirect and it.objgen in seen):
            continue
        if it.is_indirect:
            seen.add(it.objgen)
        out.append(it)
        stack += list(reversed(_items(it.get("/K"))))
    return out


def _table_of(ctx: _Ctx, cell: Dictionary) -> Dictionary | None:
    p, hops = cell.get("/P"), 0
    while isinstance(p, Dictionary) and hops < 4:
        if _role_of(ctx, p) == "Table":
            return p
        p, hops = p.get("/P"), hops + 1
    return None


def _grid(ctx: _Ctx, table: Dictionary) -> dict:
    """Where each cell of a table sits, from its rows and spans: objgen -> (row, col, row_span, col_span)."""
    rows = []
    for k in _items(table.get("/K")):
        role = _role_of(ctx, k) if _is_elem_dict(k) else None
        if role == "TR":
            rows.append(k)
        elif role in ("THead", "TBody", "TFoot"):
            rows += [r for r in _items(k.get("/K")) if _is_elem_dict(r) and _role_of(ctx, r) == "TR"]
    taken, out = set(), {}
    for r, tr in enumerate(rows):
        c = 0
        for cell in _items(tr.get("/K")):
            if not _is_elem_dict(cell) or _role_of(ctx, cell) not in ("TH", "TD") or not cell.is_indirect:
                continue
            while (r, c) in taken:
                c += 1
            a = table_attrs(cell)
            rs = max(1, min(100, int(a.get("/RowSpan", 1)))) if a is not None else 1
            cs = max(1, min(100, int(a.get("/ColSpan", 1)))) if a is not None else 1
            out[cell.objgen] = (r, c, rs, cs)
            taken |= {(r + i, c + j) for i in range(rs) for j in range(cs)}
            c += cs
    return out


def _cells_of(ctx: _Ctx, table: Dictionary) -> list[Dictionary]:
    """The table's own cells (not those of a table nested in one of them)."""
    out = []
    for x in _walk(table):
        if _role_of(ctx, x) in ("TH", "TD"):
            t = _table_of(ctx, x)
            if t is not None and t.objgen == table.objgen:
                out.append(x)
    return out


def _give_id(ctx: _Ctx, cell: Dictionary, want: str) -> bytes:
    if id_of(cell):
        return id_of(cell)
    used = {id_of(x) for x in _walk(ctx.root)}
    name, k = want.encode(), 1
    while name in used:
        k += 1
        name = f"{want}-{k}".encode()
    cell.ID = String(name)
    ctx.ids_changed = True
    return name


def _set_named(cell: Dictionary, ids: list[bytes]) -> None:
    a = table_attrs(cell)
    if not ids:
        if a is not None and "/Headers" in a:
            del a["/Headers"]
        return
    if a is None:
        a = Dictionary(O=Name.Table)
        attrs = cell.get("/A")
        cell.A = a if attrs is None else Array((list(attrs) if isinstance(attrs, Array) else [attrs]) + [a])
    a.Headers = Array([String(i) for i in ids])


def _order(ids: list[bytes], cell_pos: tuple, pos_of_id: dict) -> list[bytes]:
    """Column headers (above the cell) top down, then row headers (to its left) left to right, then any others as
    they were."""
    def key(i):
        p = pos_of_id.get(i)
        if p is None:
            return (2, 0, 0)
        return (0, p[0], p[1]) if p[0] < cell_pos[0] else (1, p[1], p[0])
    return sorted(ids, key=key)


def _relink(ctx: _Ctx, cell: Dictionary, was: str | None, role: str, scope: str | None) -> int:
    """After a header/data change in a table whose cells name their headers: keep the names right. Returns how many
    cells' headers changed."""
    table = _table_of(ctx, cell)
    if table is None:
        return 0
    cells = _cells_of(ctx, table)
    if not any(headers_named(x) for x in cells):
        return 0  # the table uses Scope alone
    changed = 0
    if was == "TH" and role == "TD" and id_of(cell):
        gone = id_of(cell)
        for x in cells:
            named = headers_named(x)
            if gone in named:
                _set_named(x, [i for i in named if i != gone])
                changed += 1
        del cell["/ID"]
        ctx.ids_changed = True
    elif was == "TD" and role == "TH":
        grid = _grid(ctx, table)
        if cell.objgen not in grid:
            return 0
        r, c, rs, cs = grid[cell.objgen]
        mine = _give_id(ctx, cell, f"h{r}c{c}-{cell.objgen[0]}")
        pos_of_id = {id_of(x): grid[x.objgen] for x in cells if id_of(x) and x.objgen in grid}
        for x in cells:
            if x.objgen == cell.objgen or x.objgen not in grid:
                continue
            xr, xc, xrs, xcs = grid[x.objgen]
            below = scope in ("Column", "Both") and xr >= r + rs and set(range(xc, xc + xcs)) & set(range(c, c + cs))
            right = scope in ("Row", "Both") and xc >= c + cs and set(range(xr, xr + xrs)) & set(range(r, r + rs))
            named = headers_named(x)
            if (below or right) and mine not in named:
                _set_named(x, _order(named + [mine], (xr, xc), pos_of_id))
                changed += 1
    return changed


def _link_note(ctx: _Ctx, e: Elem, note: Elem | None, n: int) -> str:
    """The tag points at a note (/Ref, PDF 2.0), or at nothing when note is None. The note is given the ID a note
    needs if it has none."""
    if not e.obj.is_indirect or _role_of(ctx, e.obj) == "Note":
        raise EditError(f"Edit {n}: a note can't point at a note. Choose the mark or the text it's in.")
    if note is None:
        if "/Ref" in e.obj:
            del e.obj["/Ref"]
        return f"element {e.index}: no longer points at a note"
    if _role_of(ctx, note.obj) != "Note" or not note.obj.is_indirect:
        raise EditError(f"Edit {n}: element {note.index} isn't a note. Change it to a Note first.")
    _give_id(ctx, note.obj, f"note-{note.obj.objgen[0]}")
    e.obj.Ref = Array([note.obj])
    return f"element {e.index}: points at note {note.index}"


def _set_headers(ctx: _Ctx, e: Elem, heads: list[Elem], n: int) -> str:
    """The header cells a cell is read with: Headers naming them, each given an ID if it has none."""
    cell = e.obj
    if _role_of(ctx, cell) not in ("TH", "TD") or not cell.is_indirect:
        raise EditError(f"Edit {n}: only a table cell has headers.")
    table = _table_of(ctx, cell)
    for h in heads:
        if _role_of(ctx, h.obj) != "TH":
            raise EditError(f"Edit {n}: element {h.index} isn't a header cell, so it can't be one of the headers.")
        other = _table_of(ctx, h.obj) if h.obj.is_indirect else None
        if table is None or other is None or other.objgen != table.objgen:
            raise EditError(f"Edit {n}: element {h.index} is in another table.")
    grid = _grid(ctx, table) if table is not None else {}
    ids = []
    for h in heads:
        pos = grid.get(h.obj.objgen)
        ids.append(_give_id(ctx, h.obj, f"h{pos[0]}c{pos[1]}-{h.obj.objgen[0]}" if pos else f"h-{h.obj.objgen[0]}"))
    _set_named(cell, ids)
    names = ", ".join(str(h.index) for h in heads)
    return f"element {e.index}: read with header(s) {names}" if heads else f"element {e.index}: headers removed"


def _subtree(ctx: _Ctx, e: Elem) -> list[Elem]:
    out, stack = [], [e.index]
    while stack:
        x = ctx.elems[stack.pop()]
        out.append(x)
        stack.extend(x.kids)
    return out


def _parent_obj(ctx: _Ctx, e: Elem) -> Dictionary:
    p = e.obj.get("/P")
    if isinstance(p, Dictionary) and p is not None:
        return p
    return ctx.root if e.parent is None else ctx.elems[e.parent].obj


def _same(a, b) -> bool:
    if isinstance(a, Dictionary) and isinstance(b, Dictionary):
        if a.is_indirect and b.is_indirect:
            return a.objgen == b.objgen
        return bool(a.get(_MARK)) and bool(b.get(_MARK))
    return False


def _detach(ctx: _Ctx, e: Elem) -> Dictionary:
    """Remove e from its parent's /K; returns the parent."""
    parent = _parent_obj(ctx, e)
    if not e.obj.is_indirect:
        e.obj[_MARK] = True
    kids = _items(parent.get("/K"))
    remaining = [k for k in kids if not _same(k, e.obj)]
    if len(remaining) == len(kids):
        raise EditError(f"element {e.index} wasn't found under its parent; the tag tree may be damaged")
    parent.K = Array(remaining)
    if _MARK in e.obj:
        del e.obj[_MARK]
    return parent


def _role_of(ctx: _Ctx, obj) -> str | None:
    """A tag's standard role, through the tree's RoleMap (PowerPoint's "Textbox" is a Sect)."""
    if not isinstance(obj, Dictionary) or obj.get("/S") is None:
        return None
    role = str(obj.S)[1:]
    rm = ctx.root.get("/RoleMap")
    seen = set()
    while role not in STANDARD and isinstance(rm, Dictionary) and isinstance(rm.get("/" + role), Name) and role not in seen:
        seen.add(role)
        role = str(rm["/" + role])[1:]
    return role


def _move(ctx: _Ctx, e: Elem, t: Elem, position: str, n: int) -> str:
    if t.index in {x.index for x in _subtree(ctx, e)}:
        raise EditError(f"Edit {n}: an element can't be moved inside itself.")
    old_parent, new_parent = _parent_obj(ctx, e), _parent_obj(ctx, t)
    at_root = lambda p: p is ctx.root or (p.is_indirect and ctx.root.is_indirect and p.objgen == ctx.root.objgen)  # noqa: E731
    v = check_move(e.role, None if at_root(old_parent) else _role_of(ctx, old_parent),
                   None if at_root(new_parent) else _role_of(ctx, new_parent), _same(old_parent, new_parent))
    if not v.ok:
        raise EditError(f"Edit {n}: element {e.index} ({e.role}) can't go there. {v.reason}")
    _detach(ctx, e)
    new_parent = _parent_obj(ctx, t)
    if not t.obj.is_indirect:
        t.obj[_MARK] = True
    kids = _items(new_parent.get("/K"))
    at = next((i for i, k in enumerate(kids) if _same(k, t.obj)), None)
    if _MARK in t.obj:
        del t.obj[_MARK]
    if at is None:
        raise EditError(f"Edit {n}: the target element wasn't found under its parent.")
    kids.insert(at if position == "before" else at + 1, e.obj)
    new_parent.K = Array(kids)
    e.obj.P = new_parent
    return f"element {e.index} ({e.role}) moved {position} element {t.index} ({t.role})"


def _make_decorative(ctx: _Ctx, e: Elem, n: int) -> str:
    """Turn an element and everything under it into an artifact: its content's marked-content
    sequences become /Artifact (no MCID), the ParentTree entries are cleared and the tag is removed."""
    if e.parent is None and len(_items(ctx.root.get("/K"))) <= 1:
        raise EditError(f"Edit {n}: the document's only top-level tag can't be made decorative.")
    members = _subtree(ctx, e)
    if any(x.objrs for x in members):
        raise EditError(f"Edit {n}: this tag contains a link or form field, which can't be decorative.")
    changed = _to_artifacts(ctx, {x.index for x in members})
    _detach(ctx, e)
    return f"element {e.index} ({e.role}) marked decorative ({changed} content item(s) now artifacts)"


def _to_artifacts(ctx: _Ctx, member_ids: set[int]) -> int:
    """The content these tags hold directly becomes /Artifact (no MCID), and leaves the ParentTree."""
    targets: dict[tuple, set[int]] = {}
    for (owner, mcid), users in ctx.checker.index.refs.items():
        if any(u in member_ids for u in users):
            targets.setdefault(owner, set()).add(mcid)
    changed = 0
    for owner_og, mcids in targets.items():
        owner = ctx.checker.stream_obj.get(owner_og)
        if owner is None:
            continue
        ops = list(pikepdf.parse_content_stream(owner))
        touched = False
        for i, ins in enumerate(ops):
            if (isinstance(ins, ContentStreamInstruction) and str(ins.operator) == "BDC" and len(ins.operands) == 2
                    and isinstance(ins.operands[1], Dictionary) and mcid_of(ins.operands[1]) in mcids):
                ops[i] = ContentStreamInstruction([Name.Artifact, Dictionary(Type=Name.Layout)], Operator("BDC"))
                touched = True
                changed += 1
        if touched:
            data = pikepdf.unparse_content_stream(ops)
            if isinstance(owner, pikepdf.Stream):
                owner.write(data)
            else:
                owner.Contents = ctx.pdf.make_stream(data)
        _clear_parent_tree(ctx, owner, mcids)
    for key in [k for k, users in ctx.checker.index.refs.items() if any(u in member_ids for u in users)]:
        del ctx.checker.index.refs[key]
    return changed


def _delete(ctx: _Ctx, e: Elem, children: str, n: int) -> str:
    """Delete a tag. "delete": with everything in it (its content becomes artifacts). "lift": only the tag; its
    child tags take its place under its parent, and content it holds directly joins the parent if the parent holds
    content, else becomes an artifact. Never leaves content untagged or a tree that isn't valid."""
    parent = _parent_obj(ctx, e)
    at_root = parent is ctx.root or (parent.is_indirect and ctx.root.is_indirect and parent.objgen == ctx.root.objgen)
    if children == "delete":
        if at_root and len(_items(ctx.root.get("/K"))) <= 1:
            raise EditError(f"Edit {n}: the document's only top-level tag can't be deleted.")
        members = _subtree(ctx, e)
        if e.objrs:
            raise EditError(f"Edit {n}: this tag is a link or form field itself, which has to stay tagged.")
        # Links and form fields have to stay tagged: each is kept, whole, and takes the deleted tag's place
        kept = _kept_holders(ctx, e)
        parent_role = _role_of(ctx, parent)
        for h in kept:
            v = check_move(h.role, _role_of(ctx, _parent_obj(ctx, h)) or "", parent_role, False)
            if not v.ok:
                raise EditError(f"Edit {n}: this tag holds a {h.role} tag (a link or form field), which has to stay "
                                f"tagged and can't move up into the {parent_role}. {v.reason} Move it out first.")
        kept_ids = {x.index for h in kept for x in _subtree(ctx, h)}
        kept_objs = [h.obj for h in kept]
        for h in kept:
            _detach(ctx, h)
        changed = _to_artifacts(ctx, {x.index for x in members} - kept_ids)
        if not e.obj.is_indirect:
            e.obj[_MARK] = True
        siblings = _items(parent.get("/K"))
        at = next((i for i, k in enumerate(siblings) if _same(k, e.obj)), None)
        if _MARK in e.obj:
            del e.obj[_MARK]
        if at is None:
            raise EditError(f"element {e.index} wasn't found under its parent; the tag tree may be damaged")
        for obj in kept_objs:
            obj.P = parent
        parent.K = Array(siblings[:at] + kept_objs + siblings[at + 1:])
        gone = len(members) - 1 - len(kept_ids)
        return (f"element {e.index} ({e.role}) deleted with {gone} tag(s) inside it "
                f"({changed} content item(s) now artifacts)"
                + (f"; {len(kept)} link(s) and form field(s) kept and moved up into the {parent_role}" if kept else ""))

    if at_root:
        raise EditError(f"Edit {n}: the document's top tag can't be removed.")
    parent_role = _role_of(ctx, parent)
    items = _items(e.obj.get("/K"))
    kids = [it for it in items if _is_elem_dict(it)]
    own = [it for it in items if not _is_elem_dict(it)]
    for kid in kids:
        v = check_move(_role_of(ctx, kid) or "", e.role, parent_role, False)
        if not v.ok:
            raise EditError(f"Edit {n}: the tags inside element {e.index} ({e.role}) can't move up a level. {v.reason} "
                            "Delete it with everything in it, or change what's around it first.")
    if any(isinstance(it, Dictionary) and "/Obj" in it for it in own):
        raise EditError(f"Edit {n}: this tag holds a link or form field directly, which has to stay tagged.")
    keeps_content = bool(own) and parent_role in CONTENT_ROLES
    moved = _reparent_items(ctx, e, items if keeps_content else kids, parent)
    artifacts = 0
    if own and not keeps_content:
        e.obj.K = Array(own)  # only what it holds directly: the child tags have already moved
        artifacts = _to_artifacts(ctx, {e.index})
    if not e.obj.is_indirect:
        e.obj[_MARK] = True
    siblings = _items(parent.get("/K"))
    at = next((i for i, k in enumerate(siblings) if _same(k, e.obj)), None)
    if _MARK in e.obj:
        del e.obj[_MARK]
    if at is None:
        raise EditError(f"element {e.index} wasn't found under its parent; the tag tree may be damaged")
    parent.K = Array(siblings[:at] + moved + siblings[at + 1:])
    if keeps_content:
        for key, users in ctx.checker.index.refs.items():
            if e.index in users and e.parent is not None:
                ctx.checker.index.refs[key] = [e.parent if u == e.index else u for u in users]
    return (f"element {e.index} ({e.role}) removed; {len(kids)} tag(s) moved up a level"
            + (f", its own content now part of the {parent_role}" if keeps_content
               else f", {artifacts} content item(s) of its own now artifacts" if artifacts else ""))


def _kept_holders(ctx: _Ctx, e: Elem) -> list[Elem]:
    """The tags under e that hold a link or a form field (the outermost of each), in reading order."""
    out: list[Elem] = []

    def walk(x: Elem) -> None:
        for k in x.kids:
            kid = ctx.elems[k]
            if kid.objrs:
                out.append(kid)  # with everything inside it (the link's own text)
            else:
                walk(kid)
    walk(e)
    return out


def _clear_parent_tree(ctx: _Ctx, owner, mcids: set[int]) -> None:
    key = owner.get("/StructParents") if owner is not None else None
    pt = ctx.root.get("/ParentTree")
    if key is None or not isinstance(pt, Dictionary):
        return
    arr = _number_tree_get(pt, int(key))
    if isinstance(arr, Array):
        for m in mcids:
            if m < len(arr):
                arr[m] = pikepdf.Object.parse(b"null")


def _number_tree_get(node, key: int, depth: int = 0):
    if depth > 32 or not isinstance(node, Dictionary):
        return None
    nums = node.get("/Nums")
    if isinstance(nums, Array):
        for i in range(0, len(nums) - 1, 2):
            if int(nums[i]) == key:
                return nums[i + 1]
    for kid in _items(node.get("/Kids")):
        lim = kid.get("/Limits") if isinstance(kid, Dictionary) else None
        if isinstance(lim, Array) and len(lim) == 2 and not (int(lim[0]) <= key <= int(lim[1])):
            continue
        found = _number_tree_get(kid, key, depth + 1)
        if found is not None:
            return found
    return None


# --- merge and split --------------------------------------------------------------------------------

def _page_obj(ctx: _Ctx, e: Elem):
    return ctx.pdf.pages[e.page].obj if e.page is not None else None


def _ancestors(ctx: _Ctx, e: Elem) -> set[int]:
    out, p = set(), e.parent
    while p is not None:
        out.add(p)
        p = ctx.elems[p].parent
    return out


def _set_number_tree(node, key: int, value, depth: int = 0) -> bool:
    if depth > 32 or not isinstance(node, Dictionary):
        return False
    nums = node.get("/Nums")
    if isinstance(nums, Array):
        for i in range(0, len(nums) - 1, 2):
            if int(nums[i]) == key:
                nums[i + 1] = value
                return True
    for kid in _items(node.get("/Kids")):
        if _set_number_tree(kid, key, value, depth + 1):
            return True
    return False


def _reparent_items(ctx: _Ctx, owner_elem: Elem, items: list, new_parent: Dictionary) -> list:
    """Point everything in items at new_parent: content (ParentTree), links (ParentTree) and child tags (/P).
    Integer MCIDs become explicit marked-content references with their page, since the new parent may be
    on another page."""
    pt = ctx.root.get("/ParentTree")
    page = _page_obj(ctx, owner_elem)
    out = []
    for item in items:
        if _is_elem_dict(item):
            item.P = new_parent
            out.append(item)
            continue
        if isinstance(item, int) or (isinstance(item, Dictionary) and "/MCID" in item):
            if isinstance(item, int):
                if page is None:
                    raise EditError(f"element {owner_elem.index} has content without a page; it can't be moved")
                item = Dictionary(Type=Name.MCR, Pg=page, MCID=item)
            elif "/Pg" not in item and "/Stm" not in item and page is not None:
                item.Pg = page
            owner = item.get("/Stm") if item.get("/Stm") is not None else item.get("/Pg")
            key = owner.get("/StructParents") if owner is not None else None
            if key is not None and isinstance(pt, Dictionary):
                arr = _number_tree_get(pt, int(key))
                mcid = mcid_of(item)
                if mcid is not None and isinstance(arr, Array) and mcid < len(arr):
                    arr[mcid] = new_parent
            out.append(item)
            continue
        if isinstance(item, Dictionary) and "/Obj" in item:
            key = item.Obj.get("/StructParent")
            if key is not None and isinstance(pt, Dictionary):
                _set_number_tree(pt, int(key), new_parent)
            out.append(item)
            continue
        out.append(item)
    return out


def _is_elem_dict(item) -> bool:
    return isinstance(item, Dictionary) and "/S" in item and item.get("/Type") not in (Name.MCR, Name.OBJR)


def _merge(ctx: _Ctx, e: Elem, others: list[Elem], n: int) -> str:
    """Move the content of `others` into e, in order, and remove their tags."""
    if not e.obj.is_indirect:
        raise EditError(f"Edit {n}: this tag can't take content from others.")
    blocked = _ancestors(ctx, e) | {x.index for x in _subtree(ctx, e)}
    for o in others:
        if o.index in blocked or e.index in _ancestors(ctx, o):
            raise EditError(f"Edit {n}: a tag can't be merged with a tag that contains it or sits inside it.")
    kids = _items(e.obj.get("/K"))
    for o in others:
        moved = _reparent_items(ctx, o, _items(o.obj.get("/K")), e.obj)
        kids += moved
        o.obj.K = Array([])
        _detach(ctx, o)
    e.obj.K = Array(kids)
    return f"element {e.index} ({e.role}) merged with " + ", ".join(f"{o.index} ({o.role})" for o in others)


# --- lists made from several tags, and their levels ----------------------------------------------------

def _at_root(ctx: _Ctx, obj) -> bool:
    return obj is ctx.root or (obj.is_indirect and ctx.root.is_indirect and obj.objgen == ctx.root.objgen)


def _make_list(ctx: _Ctx, e: Elem, others: list[Elem], n: int) -> str:
    """e and `others` become the items of one list, in that order, where e was. A text tag becomes an item
    itself (LI > LBody), so later edits that name it find the item; a list gives its items and goes."""
    tags = [e] + others
    for x in tags:
        if not x.obj.is_indirect:
            raise EditError(f"Edit {n}: element {x.index} can't become a list item.")
        if _role_of(ctx, x.obj) == "L":
            continue
        if x.role not in LIST_ITEM_FROM:
            raise EditError(f"Edit {n}: a {x.role} can't become a list item. Select paragraphs, headings or lists.")
        v = check_role("LI", x.role, "L", [ctx.elems[k].role for k in x.kids], has_content=bool(x.content_refs or x.objrs))
        if not v.ok:
            raise EditError(f"Edit {n}: element {x.index} ({x.role}): {v.reason}")
    for x in tags:
        inside = _ancestors(ctx, x)
        if any(y.index in inside for y in tags):
            raise EditError(f"Edit {n}: a tag can't join a list with a tag that contains it.")
    parent = _parent_obj(ctx, e)
    if not _at_root(ctx, parent):
        v = check_move("L", _role_of(ctx, parent), _role_of(ctx, parent), True)
        if not v.ok:
            raise EditError(f"Edit {n}: a list can't be made here. {v.reason}")

    if _role_of(ctx, e.obj) == "L":
        lst, items = e.obj, _items(e.obj.get("/K"))
    else:
        # The new list takes e's place under its parent, and e becomes its first item
        lst = ctx.pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.L, P=parent,
                                               A=Dictionary(O=Name.List, ListNumbering=Name("/None"))))
        if "/Pg" in e.obj:
            lst.Pg = e.obj.Pg
        siblings = _items(parent.get("/K"))
        pos = next((i for i, k in enumerate(siblings) if _same(k, e.obj)), None)
        if pos is None:
            raise EditError(f"Edit {n}: the tag wasn't found under its parent.")
        siblings[pos] = lst
        parent.K = Array(siblings)
        _as_item(ctx, e, n)
        e.obj.P = lst
        items = [e.obj]
    for o in others:
        if _role_of(ctx, o.obj) == "L":
            items += _reparent_items(ctx, o, _items(o.obj.get("/K")), lst)
            o.obj.K = Array([])
            _detach(ctx, o)
        else:
            _detach(ctx, o)
            _as_item(ctx, o, n)
            o.obj.P = lst
            items.append(o.obj)
    lst.K = Array(items)
    return f"element {e.index}: one list made of {len(tags)} tags"


def _as_item(ctx: _Ctx, e: Elem, n: int) -> None:
    """Turn a text tag into a list item holding what it held."""
    if e.content_refs or e.objrs or not all(ctx.elems[k].role in ("Lbl", "LBody") for k in e.kids) or not e.kids:
        _wrap(ctx, e, "LI", n)
    else:  # it already holds only a label and a body
        e.obj.S = Name.LI


def _build_list(ctx: _Ctx, e: Elem, others: list[Elem], rows: list[dict], n: int) -> str:
    """Cut the content of e (and of `others`, which follow it) into rows, and make each row a tag of its own or
    an item of a list, nested by level. Everything takes e's place under its parent, in order."""
    tags = [e] + others
    for x in tags:
        if not x.obj.is_indirect or x.role not in LIST_ITEM_FROM:
            raise EditError(f"Edit {n}: a {x.role} can't be built into a list. Select paragraphs or headings.")
        v = check_role("LI", x.role, "L", [ctx.elems[k].role for k in x.kids], has_content=bool(x.content_refs or x.objrs))
        if not v.ok:
            raise EditError(f"Edit {n}: element {x.index} ({x.role}): {v.reason}")
        for key, what in (("/ActualText", "read-aloud text"), ("/Alt", "alt text")):
            if key in x.obj:  # it stands for all of the tag's content, and would be wrong on any one piece
                raise EditError(f"Edit {n}: element {x.index} has {what} that stands for all of it. Remove that "
                                "first, then build the list.")
    for x in tags:
        inside = _ancestors(ctx, x)
        if any(y.index in inside for y in tags):
            raise EditError(f"Edit {n}: a tag can't be built into a list with a tag that contains it.")
    parent = _parent_obj(ctx, e)
    if not _at_root(ctx, parent):
        for role in sorted({"L" if "level" in r else r["as"] for r in rows}):
            v = check_move(role, _role_of(ctx, parent), _role_of(ctx, parent), True)
            if not v.ok:
                raise EditError(f"Edit {n}: that can't be built here. {v.reason}")

    # Every /K item of the tags, in order, and where each row starts among them
    content = [(x, item) for x in tags for item in _items(x.obj.get("/K"))]
    place, at = {}, 0
    for x in tags:
        for k in range(len(_items(x.obj.get("/K")))):
            place[(x.index, k)] = at
            at += 1
    starts = []
    for row in rows:
        pos = place.get((row.get("el", e.index), row["at"]))
        if pos is None or (starts and pos <= starts[-1]) or (not starts and pos != 0):
            raise EditError(f"Edit {n}: the rows don't match this tag's content: each has to start after the one "
                            "before it, the first at the start. Reload and try again.")
        starts.append(pos)

    def holder(role: str, owner: Elem, under: Dictionary, reuse: bool) -> Dictionary:
        """A new tag for a row (or e's own tag, for the first row), with the language its content was in."""
        if reuse:
            e.obj.S = Name("/" + role)
            e.obj.P = under
            return e.obj
        new = ctx.pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name("/" + role), P=under))
        for key in ("/Lang", "/Pg"):
            if key in owner.obj:
                new[key] = owner.obj[key]
        return new

    def take(chunk: list, into: Dictionary) -> Array:
        k: list = []
        for owner, item in chunk:
            k += _reparent_items(ctx, owner, [item], into)
        return Array(k)

    out: list[Dictionary] = []          # what takes e's place
    open_lists: list[tuple[Dictionary, list]] = []   # the list being filled at each level, and its items
    last_body: Dictionary | None = None  # the body of the item made last, where a deeper list goes
    made_tags = made_items = 0
    for i, (row, start) in enumerate(zip(rows, starts)):
        chunk = content[start:starts[i + 1] if i + 1 < len(rows) else len(content)]
        owner = chunk[0][0]
        if "as" in row:
            tag = holder(row["as"], owner, parent, reuse=i == 0)
            tag.K = take(chunk, tag)
            out.append(tag)
            open_lists, last_body = [], None
            made_tags += 1
            continue
        level = row["level"]
        if level > len(open_lists) + 1:
            raise EditError(f"Edit {n}: a list item can only be one level deeper than the item before it.")
        del open_lists[level:]
        if level > len(open_lists):  # a new list: at the top, or nested at the end of the item before
            under = parent if not open_lists else last_body
            lst = ctx.pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.L, P=under, K=Array([]),
                                                   A=Dictionary(O=Name.List, ListNumbering=Name("/None"))))
            if "/Pg" in owner.obj:
                lst.Pg = owner.obj.Pg
            if open_lists:
                under.K = Array(_items(under.get("/K")) + [lst])
            else:
                out.append(lst)
            open_lists.append((lst, []))
        lst, items = open_lists[-1]
        li = holder("LI", owner, lst, reuse=i == 0)
        body = ctx.pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.LBody, P=li))
        if "/Pg" in owner.obj:
            body.Pg = owner.obj.Pg
        body.K = take(chunk, body)
        li.K = Array([body])
        items.append(li)
        lst.K = Array(items)
        last_body = body
        made_items += 1

    for o in others:
        o.obj.K = Array([])
        _detach(ctx, o)
    siblings = _items(parent.get("/K"))
    pos = next((i for i, k in enumerate(siblings) if _same(k, e.obj)), None)
    if pos is None:
        raise EditError(f"Edit {n}: the tag wasn't found under its parent.")
    parent.K = Array(siblings[:pos] + out + siblings[pos + 1:])
    if "level" in rows[0] and "/A" in e.obj:
        del e.obj["/A"]  # a paragraph's layout attributes don't belong on a list item
    return (f"element {e.index}: built into {made_items} list item(s)"
            + (f" and {made_tags} other tag(s)" if made_tags else ""))


def _list_item(ctx: _Ctx, e: Elem, n: int, what: str) -> tuple[Dictionary, list, int]:
    """The list holding item e as it is now, that list's items, and e's place among them."""
    lst = e.obj.get("/P")
    if _role_of(ctx, e.obj) != "LI" or not e.obj.is_indirect or not isinstance(lst, Dictionary) or _role_of(ctx, lst) != "L":
        raise EditError(f"Edit {n}: only a list item can be {what}.")
    items = _items(lst.get("/K"))
    pos = next((i for i, k in enumerate(items) if _same(k, e.obj)), None)
    if pos is None:
        raise EditError(f"Edit {n}: the item wasn't found in its list.")
    return lst, items, pos


def _kid_elems(ctx: _Ctx, obj: Dictionary, role: str) -> list[Dictionary]:
    return [k for k in _items(obj.get("/K")) if _is_elem_dict(k) and _role_of(ctx, k) == role]


def _sublist_of(ctx: _Ctx, item: Dictionary, like: Dictionary) -> Dictionary:
    """The list nested at the end of a list item's body, made (with `like`'s list attributes) if there is none.
    Nested lists go inside the LBody, after its text, as the tagger builds them."""
    bodies = _kid_elems(ctx, item, "LBody")
    if bodies:
        body = bodies[-1]
    else:
        body = ctx.pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.LBody, P=item, K=Array([])))
        item.K = Array(_items(item.get("/K")) + [body])
    kids = _items(body.get("/K"))
    if kids and _is_elem_dict(kids[-1]) and _role_of(ctx, kids[-1]) == "L":
        return kids[-1]
    sub = ctx.pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.L, P=body, K=Array([])))
    sub.A = like["/A"] if "/A" in like else Dictionary(O=Name.List, ListNumbering=Name("/None"))
    body.K = Array(kids + [sub])
    return sub


def _indent(ctx: _Ctx, e: Elem, n: int) -> str:
    """A list item goes one level deeper: to the end of the list nested under the item before it."""
    lst, items, pos = _list_item(ctx, e, n, "indented")
    before = items[pos - 1] if pos else None
    role = _role_of(ctx, before) if _is_elem_dict(before) else None
    if role == "LI":
        sub = _sublist_of(ctx, before, lst)
    elif role == "L":  # a list nested directly in the list (L > L), as some producers write it
        sub = before
    else:
        raise EditError(f"Edit {n}: the first item of a list can't be indented: there's no item before it to go under.")
    lst.K = Array(items[:pos] + items[pos + 1:])
    sub.K = Array(_items(sub.get("/K")) + [e.obj])
    e.obj.P = sub
    return f"element {e.index}: list item moved one level in"


def _outdent(ctx: _Ctx, e: Elem, n: int) -> str:
    """An item of a nested list goes one level up, right after the item it was under. The items after it in
    the nested list go under it, so nothing is read in a different order."""
    sub, items, pos = _list_item(ctx, e, n, "moved out a level")
    holder = sub.get("/P")
    holder_role = _role_of(ctx, holder) if isinstance(holder, Dictionary) and not _at_root(ctx, holder) else None
    if holder_role == "LBody":
        over = holder.get("/P")       # the item it's under
        outer = over.get("/P") if isinstance(over, Dictionary) else None
        anchor = over
        tail = _items(holder.get("/K"))
        if not tail or not _same(tail[-1], sub):
            raise EditError(f"Edit {n}: the item above has text after this nested list, so moving the item out "
                            "would change the reading order. Move that text first.")
    elif holder_role == "L":
        outer, anchor = holder, sub
    else:
        outer = None
    if not isinstance(outer, Dictionary) or _role_of(ctx, outer) != "L" or (holder_role == "LBody" and _role_of(ctx, anchor) != "LI"):
        raise EditError(f"Edit {n}: this item is already at the top level of its list.")
    rest = items[pos + 1:]
    sub.K = Array(items[:pos])
    outer_items = _items(outer.get("/K"))
    at = next((i for i, k in enumerate(outer_items) if _same(k, anchor)), None)
    if at is None:
        raise EditError(f"Edit {n}: the item wasn't found in its list.")
    outer_items.insert(at + 1, e.obj)
    e.obj.P = outer
    if rest:
        under = _sublist_of(ctx, e.obj, sub)
        for item in rest:
            item.P = under
        under.K = Array(_items(under.get("/K")) + rest)
    if not _items(sub.get("/K")):  # nothing left in the nested list
        if holder_role == "LBody":
            holder.K = Array([k for k in _items(holder.get("/K")) if not _same(k, sub)])
        else:
            outer_items = [k for k in outer_items if not _same(k, sub)]
    outer.K = Array(outer_items)
    return f"element {e.index}: list item moved one level out"


# --- a list made into a table of contents -------------------------------------------------------------

def _make_toc(ctx: _Ctx, e: Elem, n: int) -> str:
    """A list becomes a table of contents. The tags change type where they are, so nothing is read in a different
    order: L -> TOC, LI -> TOCI (its label stays), the item's body -> Reference, and a list nested in the body moves
    out of it to follow the Reference in the same entry, as a TOC of its own."""
    if not e.obj.is_indirect or _role_of(ctx, e.obj) != "L":
        raise EditError(f"Edit {n}: only a list can be made into a table of contents.")
    parent = _parent_obj(ctx, e)
    if not _at_root(ctx, parent) and _role_of(ctx, parent) in ("L", "LI", "LBody", "TOC", "TOCI"):
        raise EditError(f"Edit {n}: this list is nested in another one. Make the outer list a table of contents; "
                        "its nested lists come with it.")
    elems = {x.obj.objgen: x for x in ctx.elems if x.obj.is_indirect}
    entries = _toc_from(ctx, e.obj, elems, n)
    return f"element {e.index}: list made into a table of contents of {entries} entr{'y' if entries == 1 else 'ies'}"


def _drop_list_layout(obj: Dictionary) -> None:
    """Remove the List attributes (bullet style and the like), which mean nothing on a table of contents."""
    is_list = lambda a: isinstance(a, Dictionary) and a.get("/O") == Name.List  # noqa: E731
    attrs = obj.get("/A")
    if is_list(attrs):
        del obj["/A"]
    elif isinstance(attrs, Array):
        kept, skip = [], False
        for a in attrs:
            if skip and isinstance(a, int):  # the revision number that follows an attribute dictionary
                skip = False
                continue
            skip = is_list(a)
            if not skip:
                kept.append(a)
        if kept:
            obj.A = Array(kept)
        else:
            del obj["/A"]


def _toc_from(ctx: _Ctx, lst: Dictionary, elems: dict, n: int) -> int:
    """Make a list and its items a TOC in place; returns the number of entries, nested ones included."""
    lst.S = Name.TOC
    _drop_list_layout(lst)
    entries = 0
    for item in _items(lst.get("/K")):
        role = _role_of(ctx, item) if _is_elem_dict(item) else None
        if role == "L":  # a list nested directly in the list (L > L)
            entries += _toc_from(ctx, item, elems, n)
        elif role == "LI":
            entries += 1 + _toci_from(ctx, item, elems, n)
        # A caption stays; content held directly by the list is already reported by the checks
    return entries


def _toci_from(ctx: _Ctx, li: Dictionary, elems: dict, n: int) -> int:
    li.S = Name.TOCI
    entries, kids = 0, []
    for kid in _items(li.get("/K")):
        role = _role_of(ctx, kid) if _is_elem_dict(kid) else None
        if role == "LBody":
            parts, more = _entry_parts(ctx, kid, li, elems, n)
            kids += parts
            entries += more
        else:
            if role == "L":
                entries += _toc_from(ctx, kid, elems, n)
            kids.append(kid)  # the label, or what the item held directly
    li.K = Array(kids)
    return entries


def _entry_parts(ctx: _Ctx, body: Dictionary, li: Dictionary, elems: dict, n: int) -> tuple[list, int]:
    """What an item's body becomes in its entry: its text a Reference (the body itself, retyped), a paragraph in it
    stays a paragraph, and a nested list a TOC. When text follows a nested list, that text gets a Reference of its
    own, so the order stays as it was."""
    runs: list[list] = []   # each is ["ref", items] or ["keep", tag]
    for k in _items(body.get("/K")):
        role = _role_of(ctx, k) if _is_elem_dict(k) else None
        if role in ("L", "P"):
            runs.append(["keep", k])
        elif role in BLOCKS:
            raise EditError(f"Edit {n}: a list item holds a {role}; an entry in a table of contents can hold only "
                            "a label, its text, paragraphs and nested entries. Change that tag first.")
        elif runs and runs[-1][0] == "ref":
            runs[-1][1].append(k)
        else:
            runs.append(["ref", [k]])
    out, entries, reused = [], 0, False
    owner = elems.get(body.objgen) if body.is_indirect else None
    for kind, val in runs:
        if kind == "keep":
            val.P = li
            if _role_of(ctx, val) == "L":
                entries += _toc_from(ctx, val, elems, n)
            out.append(val)
        elif not reused:
            body.S = Name.Reference
            body.K = Array(val)
            reused = True
            out.append(body)
        else:
            if owner is None:  # a body made earlier in this save: its content already names its page
                owner = Elem(-1, body, "LBody", "LBody", None, None)
            ref = ctx.pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Reference, P=li))
            for key in ("/Lang", "/Pg"):
                if key in body:
                    ref[key] = body[key]
            ref.K = Array(_reparent_items(ctx, owner, val, ref))
            out.append(ref)
    if reused:
        body.P = li
    return out, entries


def _split(ctx: _Ctx, e: Elem, at: int, n: int) -> str:
    """Move e's /K items from position `at` on into a new tag of the same type, placed right after e."""
    items = _items(e.obj.get("/K"))
    if not 1 <= at < len(items):
        raise EditError(f"Edit {n}: this tag can't be split there.")
    _new_sibling(ctx, e, items[:at], lambda new: _reparent_items(ctx, e, items[at:], new), n)
    return f"element {e.index} ({e.role}) split into two at item {at}"


def _new_sibling(ctx: _Ctx, e: Elem, keep: list, give, n: int) -> Dictionary:
    """A new tag of e's type right after e, holding give(new); e keeps `keep`."""
    parent = _parent_obj(ctx, e)
    new = ctx.pdf.make_indirect(Dictionary(Type=Name.StructElem, S=e.obj.S, P=parent))
    for key in ("/Lang", "/A"):
        if key in e.obj:
            new[key] = e.obj[key]
    if "/Pg" in e.obj:
        new.Pg = e.obj.Pg
    new.K = Array(give(new))
    e.obj.K = Array(keep)
    if not e.obj.is_indirect:
        e.obj[_MARK] = True
    siblings = _items(parent.get("/K"))
    pos = next((i for i, k in enumerate(siblings) if _same(k, e.obj)), None)
    if _MARK in e.obj:
        del e.obj[_MARK]
    if pos is None:
        raise EditError(f"Edit {n}: the tag wasn't found under its parent.")
    siblings.insert(pos + 1, new)
    parent.K = Array(siblings)
    return new


# --- splitting inside a piece of text -----------------------------------------------------------------

_SHOW = {"Tj", "'", '"', "TJ"}


def _split_inside(ctx: _Ctx, e: Elem, k: int, char: int, n: int) -> str:
    """Split e inside its k-th /K item, a piece of text, before character `char` of that piece's text.
    The marked-content sequence is closed before the glyph that starts there and a new one (new MCID)
    opened, splitting the text-showing operator if needed; the new sequence and e's later items become
    a new tag right after e."""
    cant = f"Edit {n}: this text can't be split inside; split between its pieces instead."
    items = _items(e.obj.get("/K"))
    if not 0 <= k < len(items):
        raise EditError(f"Edit {n}: this tag can't be split there.")
    item = items[k]
    if isinstance(item, int):
        mcid, pg = item, _page_obj(ctx, e)
    elif isinstance(item, Dictionary) and mcid_of(item) is not None and item.get("/Stm") is None:
        mcid, pg = mcid_of(item), item.get("/Pg") if item.get("/Pg") is not None else _page_obj(ctx, e)
    else:
        raise EditError(cant)
    if pg is None or ctx.path is None:
        raise EditError(cant)
    page_no = next((i for i, p in enumerate(ctx.pdf.pages) if p.obj.objgen == pg.objgen), None)
    key = pg.get("/StructParents")
    if page_no is None or key is None:
        raise EditError(cant)
    glyphs = _mcid_glyphs(ctx.path, page_no, mcid)
    g = _glyph_at(glyphs, char)
    if g is None:
        raise EditError(f"Edit {n}: pick a point inside the text, not at its start or end.")

    page = ctx.pdf.pages[page_no]
    instrs = list(pikepdf.parse_content_stream(page))
    used = {int(ins.operands[1].MCID) for ins in instrs
            if isinstance(ins, ContentStreamInstruction) and str(ins.operator) == "BDC" and len(ins.operands) == 2
            and isinstance(ins.operands[1], Dictionary) and "/MCID" in ins.operands[1]}
    new_mcid = max(used | {mcid}) + 1
    fonts = page.resources.get("/Font")
    out, counted = _split_stream(instrs, mcid, g, new_mcid, fonts if isinstance(fonts, Dictionary) else Dictionary())
    if out is None or counted != len(glyphs):
        raise EditError(cant)
    pg.Contents = ctx.pdf.make_stream(pikepdf.unparse_content_stream(out))

    pt = ctx.root.get("/ParentTree")
    arr = _number_tree_get(pt, int(key)) if isinstance(pt, Dictionary) else None
    if not isinstance(arr, Array):
        raise EditError(cant)

    def give(new: Dictionary) -> list:
        while len(arr) <= new_mcid:
            arr.append(pikepdf.Object.parse(b"null"))
        arr[new_mcid] = new
        return [Dictionary(Type=Name.MCR, Pg=pg, MCID=new_mcid)] + _reparent_items(ctx, e, items[k + 1:], new)

    _new_sibling(ctx, e, items[:k + 1], give, n)
    note = ""
    if "/ActualText" in e.obj:  # it described the whole text; it no longer matches either half
        del e.obj["/ActualText"]
        note = "; its read-aloud text was removed, set it again on each part if needed"
    return f"element {e.index} ({e.role}) split inside its text at character {char}{note}"


def _mcid_glyphs(path: Path, page_no: int, mcid: int) -> list[dict]:
    """The glyphs of one marked-content sequence, in drawing order (as the tag map reads them)."""
    import pdfplumber

    import app.verification.semantic  # noqa: F401 - patches pdfplumber's marked-content tracking

    with pdfplumber.open(path, pages=[page_no + 1]) as plumb:
        return [c for c in plumb.pages[0].chars if c.get("mcid") == mcid and c.get("tag") != "Artifact"]


def _glyph_at(glyphs: list[dict], char: int) -> int | None:
    """The glyph that starts at character `char` of the piece's text (as the tag map shows it: whitespace collapsed,
    a space where a line ends), or the next one; None at either end."""
    from app.verification.semantic import glyph_text

    _, starts = glyph_text(glyphs)
    return next((gi for gi, at in enumerate(starts) if gi > 0 and at >= char), None)


def _codes(font) -> int | None:
    """Bytes per character code for a font, or None when it can't be known without decoding its CMap."""
    if not isinstance(font, Dictionary):
        return None
    if font.get("/Subtype") == Name.Type0:
        return 2 if font.get("/Encoding") in (Name("/Identity-H"), Name("/Identity-V")) else None
    return 1


def _split_stream(instrs: list, mcid: int, g: int, new_mcid: int, fonts: Dictionary):
    """Close marked-content `mcid` before its g-th glyph and open `new_mcid` there. Returns the new
    instructions and the number of glyphs counted in the sequence, or (None, 0) when it can't be done."""
    out: list = []
    stack: list[bool] = []  # one entry per open marked-content section: is it the target?
    font_stack: list = []
    font = None
    seen = 0
    done = False
    props = None
    for ins in instrs:
        if not isinstance(ins, ContentStreamInstruction):
            out.append(ins)
            continue
        op = str(ins.operator)
        if op in ("BDC", "BMC"):
            target = (op == "BDC" and len(ins.operands) == 2 and isinstance(ins.operands[1], Dictionary)
                      and mcid_of(ins.operands[1]) == mcid)
            if target:
                props = (ins.operands[0], ins.operands[1])
            stack.append(target)
        elif op == "EMC":
            if stack:
                stack.pop()
        elif op == "q":
            font_stack.append(font)
        elif op == "Q":
            font = font_stack.pop() if font_stack else font
        elif op == "Tf" and ins.operands:
            font = fonts.get(ins.operands[0])
        inside = any(stack)
        if op in _SHOW and inside:
            bpc = _codes(font)
            if bpc is None:
                return None, 0
            if op == "TJ":
                strings = [bytes(x) for x in ins.operands[0] if isinstance(x, pikepdf.String)]
            else:
                strings = [bytes(ins.operands[-1])]
            count = sum(len(b) // bpc for b in strings)
            if not done and seen + count > g:
                if not stack[-1]:  # the split point is inside a nested section (e.g. a ligature's Span)
                    return None, 0
                cut = (g - seen) * bpc
                before, after = _cut_show(ins, op, cut)
                if before is not None:
                    out.append(before)
                out.append(ContentStreamInstruction([], Operator("EMC")))
                out.append(ContentStreamInstruction([props[0], _with_mcid(props[1], new_mcid)], Operator("BDC")))
                out.append(after)
                done = True
                seen += count
                continue
            seen += count
        elif op == "Do" and inside:
            return None, 0  # content drawn through a form: its glyphs aren't in this stream
        out.append(ins)
    return (out, seen) if done else (None, 0)


def _with_mcid(props: Dictionary, mcid: int) -> Dictionary:
    d = Dictionary({k: v for k, v in props.items() if k not in ("/MCID", "/ActualText", "/Alt")})
    d.MCID = mcid
    return d


def _cut_show(ins, op: str, cut: int):
    """Split a text-showing instruction `cut` bytes into its text. Drawing the two parts one after the
    other is the same as drawing the original: the pen moves on after each."""
    if op == "TJ":
        first, second, left = [], [], cut
        for x in ins.operands[0]:
            if left <= 0 and (second or isinstance(x, pikepdf.String)):
                second.append(x)
            elif isinstance(x, pikepdf.String):
                b = bytes(x)
                if len(b) <= left:
                    first.append(x)
                    left -= len(b)
                else:
                    if left:
                        first.append(pikepdf.String(b[:left]))
                    second.append(pikepdf.String(b[left:]))
                    left = 0
            else:
                first.append(x)
        before = ContentStreamInstruction([Array(first)], Operator("TJ")) if first else None
        return before, ContentStreamInstruction([Array(second)], Operator("TJ"))
    b = bytes(ins.operands[-1])
    head, tail = pikepdf.String(b[:cut]), pikepdf.String(b[cut:])
    after = ContentStreamInstruction([tail], Operator("Tj"))
    if cut == 0:
        # Split before the operator: it still has to do its line move and spacing ('/")
        return None, ContentStreamInstruction(list(ins.operands[:-1]) + [tail], ins.operator)
    return ContentStreamInstruction(list(ins.operands[:-1]) + [head], ins.operator), after


# --- areas: moving content between tags, tagging artifacts ------------------------------------------

def _page_array(ctx: _Ctx, page, create: bool = False):
    """The ParentTree array for a page's content (MCID -> tag); made if create and the page has none."""
    pt = ctx.root.get("/ParentTree")
    if not isinstance(pt, Dictionary):
        if not create:
            return None
        pt = ctx.pdf.make_indirect(Dictionary(Nums=Array()))
        ctx.root.ParentTree = pt
    key = page.get("/StructParents")
    if key is not None:
        arr = _number_tree_get(pt, int(key))
        if isinstance(arr, Array) or not create:
            return arr
    if not create:
        return None
    # A new key, one past the highest in use
    used = []

    def collect(node, depth=0):
        if depth > 32 or not isinstance(node, Dictionary):
            return
        nums = node.get("/Nums")
        if isinstance(nums, Array):
            used.extend(int(nums[i]) for i in range(0, len(nums) - 1, 2))
        for kid in _items(node.get("/Kids")):
            collect(kid, depth + 1)
    collect(pt)
    new_key = int(key) if key is not None else max(used + [int(ctx.root.get("/ParentTreeNextKey", 0)) - 1]) + 1
    arr = ctx.pdf.make_indirect(Array())
    holder = pt
    while not isinstance(holder.get("/Nums"), Array) and _items(holder.get("/Kids")):
        holder = _items(holder.get("/Kids"))[-1]
    if not isinstance(holder.get("/Nums"), Array):
        holder.Nums = Array()
    holder.Nums.extend([new_key, arr])
    if isinstance(holder.get("/Limits"), Array):
        holder.Limits = Array([min(int(holder.Limits[0]), new_key), max(int(holder.Limits[1]), new_key)])
    page.StructParents = new_key
    ctx.root.ParentTreeNextKey = max(int(ctx.root.get("/ParentTreeNextKey", 0)), new_key + 1)
    return arr


def _set_parent(arr, mcid: int, elem) -> None:
    while len(arr) <= mcid:
        arr.append(pikepdf.Object.parse(b"null"))
    arr[mcid] = elem


def _assign(ctx: _Ctx, e: Elem, pieces: list[dict], n: int) -> str:
    """Change the area a tag covers: pieces of tagged content move from their tags into this one."""
    if e.role not in CONTENT_ROLES:
        raise EditError(f"Edit {n}: a {e.role} holds other tags, not content of its own. Select a paragraph, heading, "
                        "figure, cell or list item body to give it this content.")
    moved, emptied = 0, []
    for piece in pieces:
        if piece["page"] > len(ctx.pdf.pages):
            raise EditError(f"Edit {n}: page {piece['page']} doesn't exist.")
        page = ctx.pdf.pages[piece["page"] - 1].obj
        users = ctx.checker.index.refs.get((page.objgen, piece["mcid"]))
        if not users:
            raise EditError(f"Edit {n}: that piece of content on page {piece['page']} isn't tagged (an artifact can "
                            "be tagged with \"Tag as\" instead).")
        owner = ctx.elems[users[0]]
        if owner.index == e.index:
            continue
        # Take the reference out of the tag holding it
        kept = []
        for it in _items(owner.obj.get("/K")):
            is_it = False
            if isinstance(it, int) or (not isinstance(it, Dictionary) and not isinstance(it, Array)):
                try:
                    is_it = int(it) == piece["mcid"] and _same_page(owner.obj, page)
                except (TypeError, ValueError):
                    is_it = False
            elif isinstance(it, Dictionary) and "/MCID" in it and "/Stm" not in it:
                pg = it.get("/Pg") or owner.obj.get("/Pg")
                is_it = mcid_of(it) == piece["mcid"] and pg is not None and pg.objgen == page.objgen
            if not is_it:
                kept.append(it)
        owner.obj.K = Array(kept)
        # ...and into this one: a bare MCID on its own page, else a reference naming the page
        if _same_page(e.obj, page):
            e.obj.K = Array(_items(e.obj.get("/K")) + [piece["mcid"]])
        else:
            e.obj.K = Array(_items(e.obj.get("/K")) + [Dictionary(Type=Name.MCR, Pg=page, MCID=piece["mcid"])])
            if "/Pg" not in e.obj:
                e.obj.Pg = page
        arr = _page_array(ctx, page)
        if isinstance(arr, Array):
            _set_parent(arr, piece["mcid"], e.obj)
        ctx.checker.index.refs[(page.objgen, piece["mcid"])] = [e.index]
        moved += 1
        if not kept and owner not in emptied:
            emptied.append(owner)
    # Content in the order it's drawn, when the tag holds only content
    items = _items(e.obj.get("/K"))
    if items and not any(_is_elem_dict(it) for it in items):
        page_no = {p.obj.objgen: i for i, p in enumerate(ctx.pdf.pages)}

        def order(it):
            pg = (it.get("/Pg") if isinstance(it, Dictionary) else None) or e.obj.get("/Pg")
            mcid = mcid_of(it)
            return (page_no.get(pg.objgen, 0) if pg is not None else 0, -1 if mcid is None else mcid)
        if all(isinstance(it, Dictionary) and "/MCID" in it and "/Stm" not in it or not isinstance(it, Dictionary)
               for it in items):
            e.obj.K = Array(sorted(items, key=order))
    for gone in emptied:
        if not _items(gone.obj.get("/K")) and not gone.objrs:
            _detach(ctx, gone)
    return (f"element {e.index} ({e.role}) now covers {moved} more piece(s) of content"
            + (f"; {len(emptied)} tag(s) left empty were removed" if emptied else ""))


def _take_pieces(ctx: _Ctx, e: Elem, op: dict, n: int) -> str:
    """Some of e's pieces of content become a new tag right after it (one drawing out of a figure of several)."""
    pieces = op["pieces"]
    for piece in pieces:
        page = ctx.pdf.pages[piece["page"] - 1].obj if piece["page"] <= len(ctx.pdf.pages) else None
        users = ctx.checker.index.refs.get((page.objgen, piece["mcid"])) if page is not None else None
        if not users or users[0] != e.index:
            raise EditError(f"Edit {n}: a piece of content on page {piece['page']} isn't part of this {e.role}.")
    mine = {(p, m) for (p, m), users in ctx.checker.index.refs.items() if users and users[0] == e.index}
    if len(mine) <= len({(p["page"], p["mcid"]) for p in pieces}):
        raise EditError(f"Edit {n}: that would take everything out of this {e.role}. Leave some of its content in it, "
                        "or change the tag itself instead.")
    parent = _parent_obj(ctx, e)
    new = ctx.pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name("/" + op["role"]), P=parent, K=Array([])))
    for key in ("/Pg", "/Lang"):
        if key in e.obj:
            new[key] = e.obj[key]
    if str(op.get("alt") or "").strip():
        new.Alt = String(op["alt"].strip())
    _insert_after(ctx, e, [new], n)
    # A record for the new tag, so later edits in this save find the pieces' new owner
    made = Elem(index=len(ctx.elems), obj=new, role=op["role"], raw_role=op["role"], page=e.page, parent=e.parent)
    ctx.elems.append(made)
    _assign(ctx, made, pieces, n)
    return f"element {e.index} ({e.role}): {len(pieces)} piece(s) of content made into a new {op['role']} after it"


# --- cutting an area out of a tag's content ---------------------------------------------------------------

_PATH = {"m", "l", "c", "v", "y", "h", "re"}
_PAINT = {"S", "s", "f", "F", "f*", "B", "B*", "b", "b*", "n"}
_SHOW = {"Tj", "TJ", "'", '"'}


def _mul(a: list[float], b: list[float]) -> list[float]:
    """a then b, as PDF matrices [a b c d e f]."""
    return [a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3], a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3],
            a[4] * b[0] + a[5] * b[2] + b[4], a[4] * b[1] + a[5] * b[3] + b[5]]


def _point(m: list[float], x: float, y: float) -> tuple[float, float]:
    return m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]


def _cut_objects(instrs: list, start: int, end: int, ctm: list[float]) -> list[tuple[int, int, tuple[float, float] | None]]:
    """The instructions start..end-1 (inside one marked section) as drawn things: a path up to its painting, a
    q..Q group, a text object, an image. Each is (first, last+1, its middle in PDF space, or None for what draws
    nothing on its own: colours, line widths, which go with the thing after them)."""
    out = []
    i = start
    stack = [ctm]
    pending = None  # the first of the state instructions waiting for the next drawn thing
    while i < end:
        op, _ = _op(instrs[i])
        first = pending if pending is not None else i
        if op == "q" or op == "BT":
            # a group: to its matching Q or ET, with everything it draws
            close, depth, j = ("Q" if op == "q" else "ET"), 0, i
            pts: list[tuple[float, float]] = []
            m, tm = stack[-1], [1.0, 0, 0, 1.0, 0, 0]
            inner = [m]
            while j < end:
                o, ops = _op(instrs[j])
                if o == op:
                    depth += 1
                    if o == "q":
                        inner.append(inner[-1])
                elif o == close:
                    depth -= 1
                    if o == "Q" and len(inner) > 1:
                        inner.pop()
                    if depth == 0:
                        break
                elif o == "cm" and len(ops) == 6:
                    inner[-1] = _mul(ops, inner[-1])
                elif o in _PATH and ops:
                    pts += [_point(inner[-1], ops[k], ops[k + 1]) for k in range(0, len(ops) - 1, 2)]
                    if o == "re" and len(ops) == 4:
                        pts.append(_point(inner[-1], ops[0] + ops[2], ops[1] + ops[3]))
                elif o == "Tm" and len(ops) == 6:
                    tm = ops
                elif o in ("Td", "TD") and len(ops) == 2:
                    tm = _mul([1, 0, 0, 1, ops[0], ops[1]], tm)
                elif o in _SHOW:
                    pts.append(_point(_mul(tm, inner[-1]), 0, 0))  # where the text starts
                elif o in ("Do", "INLINE"):
                    pts += [_point(inner[-1], 0, 0), _point(inner[-1], 1, 1)]  # an image fills the unit square
                j += 1
            out.append((first, j + 1, _middle(pts)))
            pending, i = None, j + 1
            continue
        if op in _PATH:
            pts = []
            j = i
            while j < end:
                o, ops = _op(instrs[j])
                if o in _PATH:
                    pts += [_point(stack[-1], ops[k], ops[k + 1]) for k in range(0, len(ops) - 1, 2)]
                    if o == "re" and len(ops) == 4:
                        pts.append(_point(stack[-1], ops[0] + ops[2], ops[1] + ops[3]))
                elif o in _PAINT:
                    break
                j += 1
            out.append((first, j + 1, _middle(pts)))
            pending, i = None, j + 1
            continue
        if op in ("Do", "INLINE"):
            out.append((first, i + 1, _middle([_point(stack[-1], 0, 0), _point(stack[-1], 1, 1)])))
            pending, i = None, i + 1
            continue
        if op == "cm":
            raise EditError("cm")  # a change of coordinates outside a q..Q group: not cut (see _cut_area)
        if pending is None:
            pending = i
        i += 1
    if pending is not None:
        out.append((pending, end, None))
    return out


def _op(ins) -> tuple[str, list[float]]:
    """An instruction's operator ("INLINE" for an inline image) and its number operands."""
    if not isinstance(ins, ContentStreamInstruction):
        return "INLINE", []
    return str(ins.operator), [float(v) for v in ins.operands if _num(v)]


def _num(v) -> bool:
    try:
        float(v)
        return not isinstance(v, (pikepdf.Name, pikepdf.String, pikepdf.Array, pikepdf.Dictionary))
    except (TypeError, ValueError):
        return False


def _middle(pts: list[tuple[float, float]]) -> tuple[float, float] | None:
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2


def _cut_area(ctx: _Ctx, e: Elem, op: dict, n: int) -> str:
    """What of e's content on a page lies inside a box becomes a new tag right after e."""
    cant = f"Edit {n}: this content can't be cut by area"
    if e.role not in CONTENT_ROLES or not e.obj.is_indirect or ctx.path is None:
        raise EditError(f"{cant}; select the figure or text that holds it.")
    if op["page"] > len(ctx.pdf.pages):
        raise EditError(f"Edit {n}: page {op['page']} doesn't exist.")
    page = ctx.pdf.pages[op["page"] - 1]
    pg = page.obj
    if int(pg.get("/Rotate", 0)) % 360:
        raise EditError(f"{cant} on a rotated page yet.")
    mine = sorted(m for (og, m), users in ctx.checker.index.refs.items() if og == pg.objgen and users and users[0] == e.index)
    if not mine:
        raise EditError(f"Edit {n}: this {e.role} has nothing on page {op['page']}.")
    # The box, from the tag map's page units (top-left origin, as the viewer draws them) to PDF space
    import pymupdf
    with pymupdf.open(ctx.path) as doc:
        to_pdf = ~doc[op["page"] - 1].transformation_matrix
    x0, y0, x1, y1 = op["bbox"]
    corners = [pymupdf.Point(x, y) * to_pdf for x, y in ((x0, y0), (x1, y1))]
    bx0, bx1 = sorted(c.x for c in corners)
    by0, by1 = sorted(c.y for c in corners)
    inside = lambda pt: pt is not None and bx0 <= pt[0] <= bx1 and by0 <= pt[1] <= by1  # noqa: E731

    instrs = list(pikepdf.parse_content_stream(page))
    used = {int(i.operands[1].MCID) for i in instrs if isinstance(i, ContentStreamInstruction) and str(i.operator) == "BDC"
            and len(i.operands) == 2 and isinstance(i.operands[1], Dictionary) and "/MCID" in i.operands[1]}
    next_mcid = max(used | set(mine)) + 1
    out: list = []
    runs: list[tuple[int, int, bool]] = []  # (original MCID, MCID of this run, inside the box)
    stack = [[1.0, 0, 0, 1.0, 0, 0]]
    i = 0
    while i < len(instrs):
        ins = instrs[i]
        o = str(ins.operator) if isinstance(ins, ContentStreamInstruction) else ""
        if o == "q":
            stack.append(stack[-1])
        elif o == "Q" and len(stack) > 1:
            stack.pop()
        elif o == "cm":
            vals = [float(v) for v in ins.operands]
            stack[-1] = _mul(vals, stack[-1])
        mcid = (int(ins.operands[1].MCID) if o == "BDC" and len(ins.operands) == 2 and isinstance(ins.operands[1], Dictionary)
                and "/MCID" in ins.operands[1] else None)
        if mcid is None or mcid not in mine:
            out.append(ins)
            i += 1
            continue
        # e's section: find its EMC, cut what's inside it into runs that are in or out of the box
        depth, j = 0, i + 1
        while j < len(instrs):
            oj = str(instrs[j].operator) if isinstance(instrs[j], ContentStreamInstruction) else ""
            if oj in ("BDC", "BMC"):
                depth += 1
            elif oj == "EMC":
                if depth == 0:
                    break
                depth -= 1
            j += 1
        if depth:
            raise EditError(f"{cant}: a marked section on that page isn't closed.")
        try:
            things = _cut_objects(instrs, i + 1, j, stack[-1])
        except EditError:
            raise EditError(f"{cant}: it changes its coordinates part way through.") from None
        sides: list[bool] = []
        for k, (_, _, mid) in enumerate(things):
            if mid is None:  # draws nothing itself: with the next thing drawn, else the last
                nxt = next((m for _, _, m in things[k + 1:] if m is not None), None)
                sides.append(inside(nxt) if nxt is not None else (sides[-1] if sides else False))
            else:
                sides.append(inside(mid))
        props = ins.operands[1]
        tag = ins.operands[0]
        k = 0
        first = True
        while k < len(things):
            side = sides[k]
            m2 = k
            while m2 + 1 < len(things) and sides[m2 + 1] == side:
                m2 += 1
            run_mcid = mcid if first else next_mcid
            if not first:
                next_mcid += 1
            p2 = Dictionary({key: props[key] for key in props.keys()})
            p2.MCID = run_mcid
            out.append(ContentStreamInstruction([tag, p2], Operator("BDC")))
            out += instrs[things[k][0]:things[m2][1]]
            out.append(ContentStreamInstruction([], Operator("EMC")))
            runs.append((mcid, run_mcid, side))
            first, k = False, m2 + 1
        if not things:
            out += instrs[i:j + 1]
            runs.append((mcid, mcid, False))
        i = j + 1
    if not any(side for _, _, side in runs):
        raise EditError(f"Edit {n}: the box holds none of this {e.role}'s content on page {op['page']}.")
    if all(side for _, _, side in runs):
        raise EditError(f"Edit {n}: the box holds all of this {e.role}'s content on page {op['page']}; change the tag "
                        "itself instead.")
    pg.Contents = ctx.pdf.make_stream(pikepdf.unparse_content_stream(out))

    # The new tag, right after e, and each run's MCID pointed at the tag that holds it
    parent = _parent_obj(ctx, e)
    new = ctx.pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name("/" + op["role"]), P=parent, K=Array([]), Pg=pg))
    if "/Lang" in e.obj:
        new.Lang = e.obj.Lang
    if str(op.get("alt") or "").strip():
        new.Alt = String(op["alt"].strip())
    _insert_after(ctx, e, [new], n)
    made = Elem(index=len(ctx.elems), obj=new, role=op["role"], raw_role=op["role"], page=op["page"] - 1, parent=e.parent)
    ctx.elems.append(made)
    arr = _page_array(ctx, pg, create=True)
    ref = lambda m, owner: m if _same_page(owner, pg) else Dictionary(Type=Name.MCR, Pg=pg, MCID=m)  # noqa: E731
    kept: list = []
    for item in _items(e.obj.get("/K")):
        m = mcid_of(item) if not isinstance(item, int) else int(item)
        on_page = isinstance(item, int) and _same_page(e.obj, pg) or (isinstance(item, Dictionary) and "/MCID" in item
                                                                         and item.get("/Stm") is None
                                                                         and (item.get("/Pg") or e.obj.get("/Pg")) is not None
                                                                         and (item.get("/Pg") or e.obj.get("/Pg")).objgen == pg.objgen)
        if not on_page or m not in mine:
            kept.append(item)
            continue
        for orig, run, side in runs:
            if orig != m:
                continue
            if side:
                new.K = Array(_items(new.get("/K")) + [ref(run, new)])
                _set_parent(arr, run, new)
                ctx.checker.index.refs[(pg.objgen, run)] = [made.index]
            else:
                kept.append(ref(run, e.obj))
                _set_parent(arr, run, e.obj)
                ctx.checker.index.refs[(pg.objgen, run)] = [e.index]
    e.obj.K = Array(kept)
    inside_n = sum(1 for _, _, side in runs if side)
    return f"element {e.index} ({e.role}): the content inside the box on page {op['page']} made into a new {op['role']} after it ({inside_n} section(s))"


def _same_page(obj, page) -> bool:
    pg = obj.get("/Pg")
    return pg is not None and pg.is_indirect and page.is_indirect and pg.objgen == page.objgen


def _tag_artifacts(ctx: _Ctx, anchor: Elem, op: dict, n: int) -> str:
    """Tag a new area: artifact sections of a page's stream become one new tag, next to the anchor tag."""
    from app.tagging.content_marker import existing_mcids

    role, wanted = op["role"], set(op["sections"])
    if op["page"] > len(ctx.pdf.pages):
        raise EditError(f"Edit {n}: page {op['page']} doesn't exist.")
    # Sections are numbered as in the tag map; ones tagged by earlier edits are no longer artifacts
    if ctx.tagged_sections is None:
        ctx.tagged_sections = {}
    done = ctx.tagged_sections.setdefault(op["page"], set())
    if wanted & done:
        raise EditError(f"Edit {n}: artifact section {min(wanted & done)} on page {op['page']} is already tagged by an "
                        "earlier change.")
    sections = {s - sum(1 for d in done if d < s) for s in wanted}
    page = ctx.pdf.pages[op["page"] - 1]
    parent = _parent_obj(ctx, anchor)
    at_root = parent is ctx.root or (parent.is_indirect and ctx.root.is_indirect and parent.objgen == ctx.root.objgen)
    v = check_move(role, "Document", None if at_root else _role_of(ctx, parent), False)
    if not v.ok:
        raise EditError(f"Edit {n}: a new {role} can't go there. {v.reason}")
    ops = list(pikepdf.parse_content_stream(page.obj))
    next_mcid = max(existing_mcids(ops) or {-1}) + 1
    new_mcids, seq, stack, found = [], 0, [], set()
    for i, ins in enumerate(ops):
        if not isinstance(ins, ContentStreamInstruction):
            continue
        name = str(ins.operator)
        if name in ("BDC", "BMC"):
            args = list(ins.operands)
            has_mcid = name == "BDC" and len(args) == 2 and isinstance(args[1], Dictionary) and "/MCID" in args[1]
            is_art = bool(args) and isinstance(args[0], Name) and str(args[0]) == "/Artifact" and not has_mcid
            chosen = False
            if is_art:
                if seq in sections:
                    if any(tag for tag, _ in stack):
                        raise EditError(f"Edit {n}: artifact section {seq} on page {op['page']} is inside another "
                                        "marked section; select the section around it instead.")
                    ops[i] = ContentStreamInstruction([Name("/" + role), Dictionary(MCID=next_mcid)], Operator("BDC"))
                    new_mcids.append(next_mcid)
                    next_mcid += 1
                    found.add(seq)
                    chosen = True
                seq += 1
            elif has_mcid and any(c for _, c in stack):
                raise EditError(f"Edit {n}: an artifact section you chose on page {op['page']} has tagged content "
                                "inside it, so it can't become a tag of its own.")
            stack.append((has_mcid or is_art, chosen))
        elif name == "EMC" and stack:
            stack.pop()
    missing = sections - found
    if missing:
        raise EditError(f"Edit {n}: page {op['page']} has no artifact section {min(missing)} (the document may have "
                        "changed; reload and try again).")
    done |= wanted
    page.obj.Contents = ctx.pdf.make_stream(pikepdf.unparse_content_stream(ops))
    elem = ctx.pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name("/" + role), P=parent, Pg=page.obj,
                                            K=Array(new_mcids)))
    alt = str(op.get("alt") or "").strip()
    if alt:
        elem.Alt = String(alt)
    arr = _page_array(ctx, page.obj, create=True)
    for m in new_mcids:
        _set_parent(arr, m, elem)
    if not anchor.obj.is_indirect:
        anchor.obj[_MARK] = True
    kids = _items(parent.get("/K"))
    at = next((k for k, it in enumerate(kids) if _same(it, anchor.obj)), None)
    if _MARK in anchor.obj:
        del anchor.obj[_MARK]
    if at is None:
        raise EditError(f"Edit {n}: the tag to place it next to wasn't found under its parent.")
    kids.insert(at if op["position"] == "before" else at + 1, elem)
    parent.K = Array(kids)
    return (f"page {op['page']}: {len(new_mcids)} artifact section(s) tagged as a new {role}, "
            f"{op['position']} element {anchor.index} ({anchor.role})")
