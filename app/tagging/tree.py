"""Small jobs on a pikepdf structure tree, shared by the tagging modules (engine refactor plan, item 2): the items of
a /K, whether something is a structure element, a walk of the tree, the standard roles and RoleMap resolution, a
number tree's entries, and new element and marked-content reference dictionaries.

app/verification keeps its own copies on purpose, so the checker shares no code with what it checks (as
content_marker.mcid_of says of integrity._mcid). The tagging and editing modules use these.
"""
from __future__ import annotations

import pikepdf
from pikepdf import Array, Dictionary, Name

# The standard structure types of PDF 1.7 (ISO 32000-1, 14.8.4)
STANDARD_TYPES = {
    "Document", "Part", "Art", "Sect", "Div", "BlockQuote", "Caption", "TOC", "TOCI", "Index",
    "NonStruct", "Private", "P", "H", "H1", "H2", "H3", "H4", "H5", "H6", "L", "LI", "Lbl", "LBody",
    "Table", "TR", "TH", "TD", "THead", "TBody", "TFoot", "Span", "Quote", "Note", "Reference",
    "BibEntry", "Code", "Link", "Annot", "Ruby", "RB", "RT", "RP", "Warichu", "WT", "WP",
    "Figure", "Formula", "Form",
}

# The roles whose content is text a kept tag can carry (a reading written onto it, a note found in it)
TEXT_ROLES = {"P", "LBody", "LI", "Lbl", "TD", "TH", "Span", "Caption", "Formula", "H", "H1", "H2", "H3", "H4", "H5",
              "H6", "Quote", "BlockQuote", "Note"}


def items(k) -> list:
    """The items of a /K (or /Kids, /Nums...) value: one object, an array of them, or nothing."""
    if k is None:
        return []
    return list(k) if isinstance(k, Array) else [k]


def kids(e) -> list:
    """The items of an element's /K."""
    return items(e.get("/K")) if isinstance(e, Dictionary) else []


def is_elem(o) -> bool:
    """A structure element: a dictionary with /S that isn't a marked-content or object reference."""
    return isinstance(o, Dictionary) and "/S" in o and o.get("/Type") not in (Name.MCR, Name.OBJR)


def walk(root) -> list:
    """Every structure element under root, in the order a reader of the file meets them (each element before its
    children, children in order), each once: the same walk on the same tree gives the same places, in memory or
    read back."""
    out, stack, seen = [], list(reversed(kids(root))), set()
    while stack:
        e = stack.pop()
        if not is_elem(e):
            continue
        if e.is_indirect:
            if e.objgen in seen:
                continue
            seen.add(e.objgen)
        out.append(e)
        stack += list(reversed(kids(e)))
    return out


def role_map(root) -> dict[str, str]:
    """A structure tree root's /RoleMap, as role names without their slash (entries mapping to a name only)."""
    rm = root.get("/RoleMap") if isinstance(root, Dictionary) else None
    if not isinstance(rm, Dictionary):
        return {}
    return {str(k)[1:]: str(v)[1:] for k, v in rm.items() if isinstance(v, Name)}


def resolve_role(role: str, mapping: dict[str, str]) -> str:
    """A role followed through the role map to a standard one (or as far as the map goes)."""
    seen = set()
    while role not in STANDARD_TYPES and role in mapping and role not in seen:
        seen.add(role)
        role = mapping[role]
    return role


def number_tree_get(node, key: int, depth: int = 0):
    """A number tree's (a ParentTree's) value for key: its /Nums, then the /Kids whose /Limits hold the key."""
    if depth > 32 or not isinstance(node, Dictionary):
        return None
    nums = node.get("/Nums")
    if isinstance(nums, Array):
        for i in range(0, len(nums) - 1, 2):
            if int(nums[i]) == key:
                return nums[i + 1]
    for kid in items(node.get("/Kids")):
        lim = kid.get("/Limits") if isinstance(kid, Dictionary) else None
        if isinstance(lim, Array) and len(lim) == 2 and not (int(lim[0]) <= key <= int(lim[1])):
            continue
        found = number_tree_get(kid, key, depth + 1)
        if found is not None:
            return found
    return None


def new_elem(pdf: pikepdf.Pdf, role: str, parent, pg=None) -> pikepdf.Object:
    """A new, empty structure element under parent (not yet among its kids), on page pg if given."""
    d = Dictionary(Type=Name.StructElem, S=Name("/" + role), P=parent, K=Array())
    if pg is not None:
        d.Pg = pg
    return pdf.make_indirect(d)


def new_mcr(page, mcid: int, stm=None) -> Dictionary:
    """A marked-content reference: the content with this MCID on page (in the form XObject stm, if given)."""
    ref = Dictionary(Type=Name.MCR, Pg=page, MCID=mcid)
    if stm is not None:
        ref.Stm = stm
    return ref
