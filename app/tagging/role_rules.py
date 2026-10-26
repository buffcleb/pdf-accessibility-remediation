"""Which tag types an element can be changed to, given where it sits and what it holds.

Some changes need the structure around the tag to change with it, and are done automatically:

  * a list (L) changed to a text type (P, H2, ...) is collapsed: each list item becomes its own tag of
    that type, holding the item's text, and lists nested in an item follow it as tags of their own;
  * a text tag changed to a list (L) is wrapped: L > LI > LBody holding the text;
  * a text tag changed to a list item (LI) inside a list is wrapped: LI > LBody holding the text.

Changes that can't make a valid tree are refused with a reason a person can act on, such as a list
item outside a list or a paragraph that holds other paragraphs. The tag manager uses the same rules (a
copy in web/src/viewer/roles.ts) to offer only the changes that will work.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.verification.integrity import ALLOWED_KIDS

HEADINGS = {"H", "H1", "H2", "H3", "H4", "H5", "H6"}
TEXT = HEADINGS | {"P", "Caption", "BlockQuote", "Note", "FENote", "Code", "Quote", "Span", "Reference", "BibEntry",
                   "Figure", "Formula"}
GROUPING = {"Document", "Part", "Art", "Sect", "Div", "NonStruct", "Private", "Form", "Index"}
# Tags that are blocks of their own: a text tag can't hold them
BLOCKS = HEADINGS | {"P", "L", "Table", "Sect", "Div", "Art", "Part", "BlockQuote", "TOC", "Index", "Document"}
# Tags that only make sense inside a particular parent
PARENT_ONLY = {
    "LI": {"L"}, "Lbl": {"LI", "TOCI"}, "LBody": {"LI"}, "TR": {"Table", "THead", "TBody", "TFoot"},
    "TH": {"TR"}, "TD": {"TR"}, "THead": {"Table"}, "TBody": {"Table"}, "TFoot": {"Table"}, "TOCI": {"TOC"},
}

NAMES = {"L": "list", "LI": "list item", "TR": "table row", "Table": "table", "TOC": "table of contents",
         "TOCI": "table of contents entry", "THead": "table header", "TBody": "table body", "TFoot": "table footer",
         "P": "paragraph"}


def _name(role: str) -> str:
    return NAMES.get(role, role)


@dataclass
class Verdict:
    ok: bool
    action: str = "none"  # none, collapse_list, wrap_list, wrap_lbody
    reason: str = ""


def check_role(new: str, role: str, parent_role: str | None, kid_roles: list[str], has_content: bool = False) -> Verdict:
    """Can an element tagged `role`, under `parent_role`, holding child tags `kid_roles` (and content of its
    own when has_content), become `new`?"""
    if new == role:
        return Verdict(True)
    kids = set(kid_roles)
    if parent_role in ALLOWED_KIDS and new not in ALLOWED_KIDS[parent_role]:
        allowed = ", ".join(sorted(ALLOWED_KIDS[parent_role]))
        return Verdict(False, reason=f"Inside a {_name(parent_role)} a tag can only be {allowed}.")
    if new in PARENT_ONLY and parent_role not in PARENT_ONLY[new]:
        where = " or ".join(_name(p) for p in sorted(PARENT_ONLY[new]))
        return Verdict(False, reason=f"A {_name(new)} has to be inside a {where}.")
    if new in ("TH", "TD"):
        return Verdict(False, reason="Use the header/data cell choice to change a table cell.")

    # A list becoming text: its items become separate tags of the new type
    if role == "L" and new in TEXT and new not in ("Span", "Quote", "Reference", "Figure", "Formula"):
        return Verdict(True, "collapse_list")

    # Children that need this tag to be of a particular kind
    for k in sorted(kids):
        if k in PARENT_ONLY and new not in PARENT_ONLY[k]:
            need = " or ".join(_name(p) for p in sorted(PARENT_ONLY[k]))
            return Verdict(False, reason=f"This tag holds {_name(k)} tags, so it has to stay a {need}.")

    if new in ALLOWED_KIDS:
        if kids <= ALLOWED_KIDS[new] and not has_content:
            return Verdict(True)
        if not kids & BLOCKS:
            if new == "L":
                return Verdict(True, "wrap_list")
            if new == "LI":
                return Verdict(True, "wrap_lbody")
        if new in ("Table", "TR", "THead", "TBody", "TFoot", "TOC"):
            return Verdict(False, reason=f"A {_name(new)} needs its own structure ({', '.join(sorted(ALLOWED_KIDS[new]))}); "
                                         "it can't be made by changing one tag.")
        return Verdict(False, reason=f"This tag holds other blocks, so it can't become a {_name(new)}.")

    if new in TEXT and kids & BLOCKS:
        return Verdict(False, reason=f"This tag holds other blocks ({', '.join(sorted(kids & BLOCKS))}); "
                                     f"a {_name(new)} can't. Change the tags inside it instead.")
    return Verdict(True)


# Tags that may only move among their own siblings: moving a cell to another row makes the rows uneven, and a
# list item's label and body belong to that item
STAY_IN_PARENT = {"TH", "TD", "Lbl", "LBody"}


def check_move(role: str, old_parent: str | None, new_parent: str | None, same_parent: bool) -> Verdict:
    """Can an element tagged `role` move from under `old_parent` to under `new_parent`?"""
    if old_parent is None:
        return Verdict(False, reason="The document's top tag can't be moved.")
    if new_parent is None:
        return Verdict(False, reason="Tags can't be placed outside the document's top tag.")
    if role in STAY_IN_PARENT and not same_parent:
        return Verdict(False, reason=("A table cell can only move within its own row: in another row it would make "
                                      "the rows uneven." if role in ("TH", "TD") else
                                      f"A list item's {'label' if role == 'Lbl' else 'body'} can only move within "
                                      "its own item."))
    if new_parent in ALLOWED_KIDS and role not in ALLOWED_KIDS[new_parent]:
        allowed = ", ".join(sorted(ALLOWED_KIDS[new_parent]))
        return Verdict(False, reason=f"Inside a {_name(new_parent)} a tag can only be {allowed}.")
    if role in PARENT_ONLY and new_parent not in PARENT_ONLY[role]:
        where = " or ".join(_name(p) for p in sorted(PARENT_ONLY[role]))
        return Verdict(False, reason=f"A {_name(role)} has to be inside a {where}.")
    if new_parent in TEXT - {"Figure", "Formula"} and role in BLOCKS:
        return Verdict(False, reason=f"A {_name(role)} can't go inside a {_name(new_parent)}.")
    return Verdict(True)
