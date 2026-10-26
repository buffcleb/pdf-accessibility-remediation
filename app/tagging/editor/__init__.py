"""Apply people's tag edits from the portal's tag manager.

Edits change the structure tree only. Content streams are untouched, so pages look exactly the same.
The one exception is "decorative", which relabels the element's marked content as an artifact; that
changes no drawing operators either.

Each operation names an element by its index in the source file's tag map (the pre-order index the
verifier and the tag map use) and the role it expects there. Every element is resolved to its PDF
object before any edit is applied, so moves can't shift the targets of later operations.

Each operation is one entry of OPERATIONS (_ctx.py): its shape check, the other tags it names, and the edit. The
modules here add their own (roles, text, tables, structure, lists, areas); a new operation is added in one place.

Operations:
    {"op": "set_role", "element": 12, "expect_role": "P", "role": "H2"}
    {"op": "set_alt", "element": 30, "expect_role": "Figure", "alt": "Bar chart of ..."}   ("" removes it)
    {"op": "set_actual_text", "element": 31, "expect_role": "Formula", "text": "x squared"}
    (set_alt and set_actual_text may carry "reading": which of a formula's two readings a person chose, #403;
    it changes nothing in the file, and the edit job records it in its report)
    {"op": "set_decorative", "element": 30, "expect_role": "Figure"}
    {"op": "set_decorative", "element": 4, "expect_role": "P", "as": "header"}
        (any tag and what's under it becomes an artifact: "layout" (the default: a rule, a decoration), or "header",
        "footer" or "page_number", marked as the page furniture it is)
    (set_alt on a Link or Annot tag also gives its annotations the text as /Contents, what a screen reader announces
    for a link; it can't be empty there)
    {"op": "set_lang", "element": 40, "expect_role": "P", "lang": "fr-CA"}                ("" removes it)
    {"op": "set_field_label", "element": 52, "expect_role": "Form", "label": "Last name"}
        (the form field's tooltip, what a screen reader announces; a radio button's own, else the field's)
    {"op": "set_doc_title", "title": "Annual report 2026"}
    {"op": "set_doc_lang", "lang": "fr-CA"}
        (the whole document's: its title, announced when it opens, and its language. No element.)
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
    {"op": "rebuild_table", "element": 70, "expect_role": "Table",
     "rows": [[{"from": [72], "role": "TH", "scope": "Column", "cols": 2}], [{"from": [74, 75], "role": "TD"},
              {"from": [], "role": "TD"}]], "caption": 66}
        (the table editor (#370): the whole table as it should be, row by row. Each cell lists the old cells whose
        content it takes, in order: one cell is kept as it was, several are merged (a row or column removed joins
        its neighbour this way), none makes an empty cell. Every old cell has to be somewhere, and the cells with
        their spans ("rows", "cols") must fill a rectangle. Header cells name their headers by ID when the table
        needs it, as when it was tagged. "caption": a tag to become the table's caption (null: none; left out:
        unchanged).)
    {"op": "build_table", "element": 20, "expect_role": "P", "absorb": [21, 22],
     "rows": [[{"parts": [{"at": 0}], "role": "TH", "scope": "Column"}, {"parts": [{"at": 1}], "role": "TH",
               "scope": "Column"}], [{"parts": [{"el": 21, "at": 0}], "role": "TD"}, ...]]}
        (text tags become a table: each cell takes /K items of the tags (the tag map's parts), every item exactly
        once; the table takes the first tag's place. A figure can be one of them, a table drawn as a picture; its alt
        text goes.)
    {"op": "make_toc", "element": 40, "expect_role": "L"}
        (a list becomes a table of contents: L -> TOC, each LI -> TOCI keeping its label, the item's text -> a
        Reference in the entry (a link in it stays inside), and a list nested in an item -> a TOC in that entry)
    {"op": "set_toc_target", "element": 41, "expect_role": "TOCI", "target": 12}
        (the heading a table of contents entry goes to: /Ref names it, as PDF/UA-2 asks (8.2.5.8); "target": null
        removes it (#631))
    take_pieces and cut_area look at what the tag is when its turn comes, so they can follow a make_list in the same
    save; expect_role is still what the tag was in the file.
"""
from __future__ import annotations

from pathlib import Path

import pikepdf
from pikepdf import Dictionary

from app.tagging.editor import areas, lists, roles, structure, tables, text
from app.tagging.editor._ctx import MAX_TEXT, OPERATIONS, EditError, Operation, _Ctx, _elem
from app.tagging.table_headers import write_id_tree
from app.tagging.tree import walk
from app.verification.integrity import IntegrityChecker

__all__ = ["DOC_OPS", "MAX_OPS", "MAX_TEXT", "OPERATIONS", "OPS", "EditError", "Operation", "apply_edits",
           "validate_ops"]

# Each module adds its operations to OPERATIONS when imported
_MODULES = (areas, lists, roles, structure, tables, text)
OPS = set(OPERATIONS)
DOC_OPS = {name for name, o in OPERATIONS.items() if not o.element}  # the document's own, naming no element
MAX_OPS = 5000


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
        o = OPERATIONS[op["op"]]
        if o.element and (not isinstance(op.get("element"), int) or op["element"] < 0):
            raise EditError(f"Edit {n}: missing element.")
        if o.validate is not None:
            o.validate(op, n)
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
            o = OPERATIONS[op["op"]]
            if not o.element:
                resolved.append((op, None, None))
                continue
            e = _elem(ctx, op["element"], op.get("expect_role"), n)
            t = o.resolve(ctx, op, n) if o.resolve is not None else None
            resolved.append((op, e, t))
        log = []
        for n, (op, e, t) in enumerate(resolved, 1):
            log.append(OPERATIONS[op["op"]].apply(ctx, op, e, t, n))
        if ctx.ids_changed:
            write_id_tree(pdf, root, walk(root))
        # The edited tree keeps tags at the right level (#438): a figure made a block, inline tags in a paragraph
        from app.tagging import structure_rules
        structure_rules.apply(pdf)
        pdf.save(dst)
    return log
