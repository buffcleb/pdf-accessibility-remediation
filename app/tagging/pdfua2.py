"""A finished PDF/UA-1 file made PDF/UA-2 (#445): the same tags, content and look, as PDF 2.0.

PDF/UA-2 asks a few things PDF/UA-1 doesn't, measured on the regression set (each a veraPDF rule):

- the file is PDF 2.0, says it is PDF/UA-2 (pdfuaid:part 2, rev 2024), and its one Document is in the PDF 2.0
  namespace (6.1, 5, 8.2.5.2);
- a footnote or endnote is FENote, PDF 2.0's note, not PDF 1.7's Note (8.2.5.14);
- a destination inside the document (a link's, a bookmark's) names the structure element it goes to, not only a
  page (8.8);
- a list whose items have labels says how they are numbered (ListNumbering, 8.2.5.25);
- a form field has a description (Contents, 8.10.2.3), and an embedded file a Desc (8.14.1);
- text that maps to private use code points has read-aloud text without them (8.4.3), and so does an artifact
  that draws such glyphs, which veraPDF counts as real content too (#615);
- a table of contents entry (TOCI) holds a label, a reference, a paragraph or a nested TOC, not text, a Span or a
  Link of its own (Table 5), and names the tag it goes to (Ref, 8.2.5.8): Word writes each entry as its links and
  text straight in the TOCI (#413).

Everything else, PDF/UA-1's own rules and the engine's repairs, carries over as it is: the step works on the file
the PDF/UA-1 run made, so the AI and tagging work is done once and a "Both" job gets two files from one run.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from pathlib import Path

import pikepdf
from pikepdf import Array, Dictionary, Name, String

from app.tagging.font_unicode import parse_tounicode
from app.tagging.tree import number_tree_get, walk

log = logging.getLogger(__name__)

PDF2_NS = "http://iso.org/pdf2/ssn"


def _kids(obj) -> list:
    k = obj.get("/K") if isinstance(obj, Dictionary) else None
    return list(k) if isinstance(k, Array) else [] if k is None else [k]


def _elems(root) -> list:
    """Every structure element under root, in tree order (each once)."""
    out, seen, stack = [], set(), list(reversed(_kids(root)))
    while stack:
        e = stack.pop()
        if not isinstance(e, Dictionary) or "/S" not in e or e.get("/Type") in (Name.MCR, Name.OBJR):
            continue
        if e.is_indirect:
            if e.objgen in seen:
                continue
            seen.add(e.objgen)
        out.append(e)
        stack += list(reversed(_kids(e)))
    return out


def _pdf2_namespace(pdf: pikepdf.Pdf, root) -> Dictionary:
    spaces = root.get("/Namespaces")
    if not isinstance(spaces, Array):
        root.Namespaces = spaces = Array()
    ns = next((n for n in spaces if isinstance(n, Dictionary) and str(n.get("/NS", "")) == PDF2_NS), None)
    if ns is None:
        ns = pdf.make_indirect(Dictionary(Type=Name.Namespace, NS=String(PDF2_NS)))
        spaces.append(ns)
    return ns


def _role(e, role_map: dict) -> str:
    """An element's standard role, through the role map (a producer's "Footnote" mapped to Note is a Note)."""
    role, seen = str(e.get("/S", ""))[1:], set()
    while role in role_map and role not in seen:
        seen.add(role)
        role = role_map[role]
    return role


def upgrade(src: Path, dst: Path, guessed: list | None = None) -> list[str]:
    """Write dst: src, a PDF/UA-1 file, as PDF/UA-2. Returns what was changed, a line each. guessed, if given, gets
    each table of contents entry whose target was found by its text, for a person to check: (its place in a walk of
    the saved tree, which the tag map and the findings number it by, the heading's text)."""
    from app.tagging.struct_tree import _list_numbering

    changes: list[str] = []
    by_text: list[tuple] = []
    with pikepdf.open(src) as pdf:
        root = pdf.Root.get("/StructTreeRoot")
        if isinstance(root, Dictionary):
            ns = _pdf2_namespace(pdf, root)
            rm = root.get("/RoleMap")
            role_map = {str(k)[1:]: str(v)[1:] for k, v in rm.items() if isinstance(v, Name)} \
                if isinstance(rm, Dictionary) else {}
            elems = _elems(root)
            top = _kids(root)
            if len(top) == 1 and isinstance(top[0], Dictionary) and top[0].get("/S") == Name.Document:
                top[0].NS = ns
            notes = 0
            for e in elems:
                if _role(e, role_map) == "Note":
                    e.S, e.NS = Name.FENote, ns
                    notes += 1
            if notes:
                changes.append(f"{notes} note(s) tagged as FENote, PDF 2.0's note")
                _notes_name_their_citations(elems)
            numbered = _number_lists(pdf, src, elems, role_map, _list_numbering)
            if numbered:
                changes.append(f"{numbered} list(s) say how their items are numbered")
            texts = _actual_text_for_private_use(pdf, src, elems)
            if texts:
                changes.append(f"{texts} tag(s) given read-aloud text for symbols with no Unicode meaning")
            dead: list = []
            dests = _structure_destinations(pdf, elems, dead)
            if dests:
                changes.append(f"{dests} link and bookmark destination(s) name the tag they go to")
            if dead:
                changes.append(_dead_links(dead))
            wrapped = _toc_items_hold_paragraphs(pdf, root, elems, role_map)
            if wrapped:
                changes.append(f"{wrapped} table of contents item(s) hold their text and links in a paragraph")
            refs = _toc_items_name_their_targets(elems, role_map)
            if refs:
                changes.append(f"{refs} table of contents item(s) name the tag they go to")
            by_text = _toc_targets_by_text(pdf, src, elems, role_map)
            if by_text:
                changes.append(f"{len(by_text)} table of contents item(s) with no link of their own pointed at the "
                               "heading of the same text, for a person to check")
            if guessed is not None and by_text:
                place = {e.objgen: n for n, e in enumerate(walk(root)) if e.is_indirect}
                guessed += [(place[t.objgen], heading) for t, heading in by_text if t.objgen in place]
        artifacts = _artifacts_with_private_use(pdf)
        if artifacts:
            changes.append(f"{artifacts} artifact(s) drawing symbols with no Unicode meaning given empty read-aloud text")
        fields = _describe_fields(pdf)
        if fields:
            changes.append(f"{fields} form field(s) given a description")
        files = _describe_files(pdf)
        if files:
            changes.append(f"{files} embedded file(s) given a description")
        with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as meta:
            meta["pdfuaid:part"] = "2"
            meta["pdfuaid:rev"] = "2024"
        changes.insert(0, "saved as PDF 2.0, labelled PDF/UA-2, its Document in the PDF 2.0 namespace")
        pdf.save(dst, force_version="2.0")
    return changes


# What a TOCI may hold in PDF 2.0 (ISO 32000-2 Annex L, Table 5 of PDF/UA-2); a nested TOCI is left where it is
_TOCI_KIDS = {"Lbl", "Reference", "NonStruct", "P", "TOC", "TOCI"}


def _toc_items_hold_paragraphs(pdf: pikepdf.Pdf, root, elems, role_map: dict) -> int:
    """Each run of what a TOCI may not hold itself (marked content, Spans, Links, annotations) put in a P of its own,
    in place: the same content in the same order, one level down. The parent tree's entries for that content name
    the P."""
    pt = root.get("/ParentTree")
    done = 0
    for e in elems:
        if _role(e, role_map) != "TOCI" or not e.is_indirect:
            continue
        kids = _kids(e)
        if all(isinstance(k, Dictionary) and "/S" in k and _role(k, role_map) in _TOCI_KIDS for k in kids):
            continue
        new_k, run = [], []

        def flush() -> None:
            if not run:
                return
            p = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.P, P=e, K=Array(run)))
            if "/Pg" in e:
                p.Pg = e.Pg
            for item in run:
                _reparent(pt, e, p, item)
            new_k.append(p)
            run.clear()

        for k in kids:
            if isinstance(k, Dictionary) and "/S" in k and _role(k, role_map) in _TOCI_KIDS:
                flush()
                new_k.append(k)
            else:
                run.append(k)
        flush()
        e.K = Array(new_k)
        done += 1
    return done


def _reparent(pt, old, new, item) -> None:
    """An item moved from element old to element new: a child element's /P, or the parent tree's entry for marked
    content (a page's or form's MCID) or an annotation."""
    if isinstance(item, Dictionary) and "/S" in item and item.get("/Type") not in (Name.MCR, Name.OBJR):
        item.P = new
        return
    if isinstance(item, int):
        owner, mcid = old.get("/Pg"), item
    elif isinstance(item, Dictionary) and item.get("/Type") == Name.MCR:
        owner = next((o for o in (item.get("/Stm"), item.get("/Pg"), old.get("/Pg")) if o is not None), None)
        mcid = int(item.MCID)
    elif isinstance(item, Dictionary) and item.get("/Type") == Name.OBJR:
        owner, mcid = item.get("/Obj"), None
    else:
        return
    key = owner.get("/StructParent" if mcid is None else "/StructParents") if isinstance(owner, Dictionary) else None
    if key is None or not isinstance(pt, Dictionary):
        return
    if mcid is None:
        _number_tree_put(pt, int(key), old, new)
        return
    arr = number_tree_get(pt, int(key))
    if isinstance(arr, Array) and 0 <= mcid < len(arr) and _same(arr[mcid], old):
        arr[mcid] = new


def _same(a, b) -> bool:
    return isinstance(a, Dictionary) and a.is_indirect and b.is_indirect and a.objgen == b.objgen


def _number_tree_put(node, key: int, old, new, depth: int = 0) -> None:
    """A number tree's value for key, where it is old, made new."""
    if depth > 32 or not isinstance(node, Dictionary):
        return
    nums = node.get("/Nums")
    if isinstance(nums, Array):
        for i in range(0, len(nums) - 1, 2):
            if int(nums[i]) == key and _same(nums[i + 1], old):
                nums[i + 1] = new
                return
    for kid in _kids_of_tree(node):
        lim = kid.get("/Limits") if isinstance(kid, Dictionary) else None
        if not (isinstance(lim, Array) and len(lim) == 2 and not int(lim[0]) <= key <= int(lim[1])):
            _number_tree_put(kid, key, old, new, depth + 1)


def _kids_of_tree(node) -> list:
    k = node.get("/Kids")
    return list(k) if isinstance(k, Array) else []


def _toc_items_name_their_targets(elems, role_map: dict) -> int:
    """Each TOCI without a Ref names the tag its link goes to: the structure destination of the first link in it
    (set by _structure_destinations), PDF/UA-2 8.2.5.8."""
    done = 0
    for e in elems:
        if _role(e, role_map) != "TOCI" or "/Ref" in e:
            continue
        target = _first_link_target(e)
        if target is not None:
            e.Ref = Array([target])
            done += 1
    return done


def _first_link_target(e, depth: int = 0):
    """The structure element the first link under e goes to, if its destination names one."""
    if depth > 50:
        return None
    for k in _kids(e):
        if isinstance(k, Dictionary) and k.get("/Type") == Name.OBJR:
            annot = k.get("/Obj")
            action = annot.get("/A") if isinstance(annot, Dictionary) else None
            sd = action.get("/SD") if isinstance(action, Dictionary) else None
            if isinstance(sd, Array) and len(sd) and isinstance(sd[0], Dictionary) and sd[0].is_indirect:
                return sd[0]
        elif isinstance(k, Dictionary) and "/S" in k:
            found = _first_link_target(k, depth + 1)
            if found is not None:
                return found
    return None


# A contents entry's or heading's text cut down to its title: dot leaders, the page number after them and a section
# number before it go ("2.1 Getting started ........ 14" and "2.1 Getting started" are both "getting started")
_LEADERS = re.compile(r"(?:[.…·_]\s*){2,}")
_PAGE_AFTER = re.compile(r"\s+(?:\d{1,4}|[ivxlc]{1,6})$", re.I)
_NUMBER_BEFORE = re.compile(r"^(?:\d{1,3}(?:\.\d{1,3})*\.?|[A-Z]\.|[ivxlc]{1,6}\.)\s+", re.I)


def _title(text: str) -> str:
    t = _LEADERS.sub(" ", " ".join(text.split()))
    t = _NUMBER_BEFORE.sub("", _PAGE_AFTER.sub("", t.strip()))
    return " ".join(re.sub(r"[^\w]+", " ", t).split()).lower()


def _subtree_content(page_of: dict, e, depth: int = 0) -> list[tuple[int, int]]:
    """The (page index, MCID) of the marked content in an element and the elements under it, in order."""
    if depth > 50:
        return []
    out = _content_of(page_of, e)
    for k in _kids(e):
        if isinstance(k, Dictionary) and "/S" in k and k.get("/Type") not in (Name.MCR, Name.OBJR):
            out += _subtree_content(page_of, k, depth + 1)
    return out


def _inside(e, role: str, role_map: dict) -> bool:
    p, seen = e.get("/P"), 0
    while isinstance(p, Dictionary) and "/S" in p and seen < 100:
        if _role(p, role_map) == role:
            return True
        p, seen = p.get("/P"), seen + 1
    return False


def _toc_targets_by_text(pdf: pikepdf.Pdf, src: Path, elems, role_map: dict) -> list[tuple]:
    """Each TOCI still without a Ref (no link of its own to follow) pointed at the one heading whose title is the
    entry's (8.2.5.8): none when no heading or more than one has it. Returns (the TOCI, the heading's text) for each,
    so a person checks the guess. Headings inside a table of contents don't count."""
    entries = [e for e in elems if _role(e, role_map) == "TOCI" and "/Ref" not in e and e.is_indirect]
    if not entries:
        return []
    heads = [e for e in elems if e.is_indirect and re.fullmatch(r"H[1-6]?", _role(e, role_map))
             and not _inside(e, "TOC", role_map)]
    page_of = _page_index(pdf)
    content = {id(e): _subtree_content(page_of, e) for e in entries + heads}
    texts = _texts(src, {c for cs in content.values() for c in cs})

    def text_of(e) -> str:
        said = e.get("/ActualText")
        return str(said) if said is not None else " ".join(texts.get(c, "") for c in content[id(e)])

    by_title: dict[str, list] = {}
    for h in heads:
        title = _title(text_of(h))
        if len(title) >= 3:
            by_title.setdefault(title, []).append(h)
    out = []
    for e in entries:
        found = by_title.get(_title(text_of(e)), [])
        if len(found) == 1:
            e.Ref = Array([found[0]])
            out.append((e, " ".join(text_of(found[0]).split())[:160]))
    return out


def _page_index(pdf: pikepdf.Pdf) -> dict:
    return {p.objgen: i for i, p in enumerate(pdf.pages)}


def _notes_name_their_citations(elems) -> None:
    """Each FENote's /Ref names every tag that names it in its own /Ref (PDF/UA-2 8.2.5.14: both ways)."""
    citing: dict[tuple, list] = {}
    for e in elems:
        ref = e.get("/Ref")
        for target in (list(ref) if isinstance(ref, Array) else [ref] if isinstance(ref, Dictionary) else []):
            if isinstance(target, Dictionary) and target.get("/S") == Name.FENote and target.is_indirect:
                citing.setdefault(target.objgen, []).append(e)
    for e in elems:
        if e.get("/S") != Name.FENote or not e.is_indirect or e.objgen not in citing:
            continue
        ref = e.get("/Ref")
        have = list(ref) if isinstance(ref, Array) else [ref] if isinstance(ref, Dictionary) else []
        known = {x.objgen for x in have if isinstance(x, Dictionary) and x.is_indirect}
        e.Ref = Array(have + [c for c in citing[e.objgen] if c.is_indirect and c.objgen not in known])


def _content_of(page_of: dict, e, forms: bool = False) -> list[tuple[int, int]]:
    """The (page index, MCID) of the marked content directly in an element (not in its child elements): on the page
    itself, or with forms, also inside the form XObjects drawn on it (an MCID there is the form's own, so only the page
    is meaningful then)."""
    pg = e.get("/Pg")
    out = []
    for k in _kids(e):
        if isinstance(k, int) and pg is not None:
            out.append((page_of.get(pg.objgen), k))
        elif isinstance(k, Dictionary) and k.get("/Type") == Name.MCR and (forms or "/Stm" not in k):
            p = k.get("/Pg", pg)
            if p is not None:
                out.append((page_of.get(p.objgen), int(k.MCID)))
    return [(p, m) for p, m in out if p is not None]


def _texts(src: Path, wanted: set[tuple[int, int]]) -> dict[tuple[int, int], str]:
    """The text of these marked-content items, as read (spaces where the page leaves gaps)."""
    if not wanted:
        return {}
    import pdfplumber

    from app.verification.semantic import glyph_text  # also gives pdfplumber's glyphs their MCID
    chars: dict[tuple[int, int], list] = {}
    with pdfplumber.open(src) as doc:
        for i in sorted({p for p, _ in wanted}):
            if i < len(doc.pages):
                for c in doc.pages[i].chars:
                    key = (i, c.get("mcid"))
                    if key in wanted and not c.get("mcid_forms"):
                        chars.setdefault(key, []).append(c)
    return {k: glyph_text(v)[0] for k, v in chars.items()}


def _number_lists(pdf, src, elems, role_map, numbering) -> int:
    """ListNumbering on each list whose items have labels and that doesn't say it yet (kept tags), from its first
    label's text."""
    page_of = _page_index(pdf)
    lists = []
    for e in elems:
        if _role(e, role_map) != "L" or _has_list_numbering(e):
            continue
        labels = [k for li in _kids(e) if isinstance(li, Dictionary) and _role(li, role_map) == "LI"
                  for k in _kids(li) if isinstance(k, Dictionary) and _role(k, role_map) == "Lbl"]
        if labels:
            lists.append((e, labels[0]))
    if not lists:
        return 0
    wanted = {pm for _, lbl in lists for pm in _content_of(page_of, lbl)}
    texts = _texts(src, wanted)
    for e, lbl in lists:
        said = str(lbl.get("/ActualText") or "") or " ".join(texts.get(pm, "") for pm in _content_of(page_of, lbl))
        # A list with labels says how they're numbered: PDF 1.7's None for dash bullets ("-", "*") is Unordered in
        # PDF 2.0, as PDF/UA-2 doesn't allow None when there are labels (8.2.5.25, #631)
        kind = numbering(said.strip() or None)
        value = Name("/" + ("Unordered" if kind == "None" else kind))
        existing = next((d for d in _attrs(e) if "/ListNumbering" in d), None)
        if existing is not None:
            existing.ListNumbering = value
            continue
        attr = Dictionary(O=Name.List, ListNumbering=value)
        a = e.get("/A")
        e.A = Array([*a, attr]) if isinstance(a, Array) else Array([a, attr]) if isinstance(a, Dictionary) else attr
    return len(lists)


def _attrs(e) -> list:
    a = e.get("/A")
    return [d for d in ([a] if isinstance(a, Dictionary) else list(a) if isinstance(a, Array) else [])
            if isinstance(d, Dictionary)]


def _has_list_numbering(e) -> bool:
    """A ListNumbering other than None (kept tags often say None for a list whose items have labels)."""
    return any(d.get("/ListNumbering") not in (None, Name("/None")) for d in _attrs(e))


_UNREADABLE = re.compile(r"\(cid:\d+\)|[\ue000-\uf8ff]")


def _actual_text_for_private_use(pdf, src, elems) -> int:
    """Text that maps to private use code points, or to nothing readable (a glyph pdfplumber can only call
    "(cid:64)", such as a TeX font's line pieces), must have read-aloud text, and that text mustn't have private use
    code points itself (PDF/UA-2 8.4.3). A tag whose own text has such glyphs and nothing that says it gets its text,
    without them, as read-aloud text; read-aloud and alt text that already has them loses them."""
    done = 0
    for e in elems:
        for key in ("/ActualText", "/Alt"):
            if key in e and _UNREADABLE.search(str(e[key])):
                e[key] = String(" ".join(_UNREADABLE.sub("", str(e[key])).split()))
                done += 1
    page_of = _page_index(pdf)
    candidates = [(e, _content_of(page_of, e)) for e in elems if "/ActualText" not in e and "/Alt" not in e]
    candidates = [(e, c) for e, c in candidates if c]
    texts = _texts(src, {pm for _, c in candidates for pm in c})
    for e, content in candidates:
        text = " ".join(t for pm in content if (t := texts.get(pm)))
        if _UNREADABLE.search(text):
            e.ActualText = String(" ".join(_UNREADABLE.sub("", text).split()))
            done += 1
    return done


_SHOWS = {"Tj", "TJ", "'", '"'}


def _private_use_codes(font, cache: dict) -> set[int]:
    """The codes of a simple font that its ToUnicode maps to private use code points (a Type0 font's: none)."""
    if not isinstance(font, Dictionary):
        return set()
    key = font.objgen if font.is_indirect else id(font)
    if key not in cache:
        tu = font.get("/ToUnicode")
        codes: set[int] = set()
        if font.get("/Subtype") != Name.Type0 and isinstance(tu, pikepdf.Stream):
            try:
                codes = {c for c, t in parse_tounicode(tu.read_bytes()).items() if _UNREADABLE.search(t)}
            except pikepdf.PdfError:
                pass
        cache[key] = codes
    return cache[key]


def _artifacts_with_private_use(pdf: pikepdf.Pdf) -> int:
    """An artifact that draws glyphs mapping to private use code points gets empty read-aloud text (ActualText ""):
    veraPDF counts glyphs inside /Artifact marked content as real content under PDF/UA-2 8.4.3, and empty read-aloud
    text is what it accepts. TeX's picture fonts (LINE10, LCIRCLE10), which draw diagrams in line pieces, are the
    usual case (#615). Pages and the forms they draw are looked at (an artifact around a form that draws such glyphs
    counts); a stream changes only when one of its artifacts does. Returns how many artifacts were given it."""
    fonts: dict = {}
    forms: dict = {}  # a form's objgen: whether it draws such glyphs outside an artifact of its own
    done = 0

    def stream(holder, resources) -> bool:
        """Mark this stream's artifacts; whether it draws such glyphs outside them."""
        nonlocal done
        own = holder.get("/Resources")
        res = own if isinstance(own, Dictionary) else resources
        font_res = res.get("/Font") if isinstance(res, Dictionary) else None
        xobjects = res.get("/XObject") if isinstance(res, Dictionary) else None
        try:
            items = list(pikepdf.parse_content_stream(holder))
        except pikepdf.PdfError:
            return False
        frames: list[tuple[int, bool]] = []  # (index of the BDC or BMC, an artifact?)
        marked: set[int] = set()
        loose = False
        font = None

        def drawn() -> None:
            nonlocal loose
            artifact = next((j for j, a in reversed(frames) if a), None)
            if artifact is None:
                loose = True
            else:
                marked.add(artifact)

        for i, item in enumerate(items):
            if isinstance(item, pikepdf.ContentStreamInlineImage):
                continue
            op = str(item.operator)
            if op in ("BDC", "BMC"):
                frames.append((i, item.operands[0] == Name.Artifact))
            elif op == "EMC" and frames:
                frames.pop()
            elif op == "Tf" and isinstance(font_res, Dictionary):
                font = font_res.get(str(item.operands[0]))
            elif op in _SHOWS and font is not None and (codes := _private_use_codes(font, fonts)):
                shown = item.operands[0] if op == "TJ" else Array([item.operands[-1]])
                if any(b in codes for s in shown if isinstance(s, String) for b in bytes(s)):
                    drawn()
            elif op == "Do" and isinstance(xobjects, Dictionary):
                form = xobjects.get(str(item.operands[0]))
                if isinstance(form, pikepdf.Stream) and form.get("/Subtype") == Name.Form:
                    if form.objgen not in forms:
                        forms[form.objgen] = False  # a guard, should a form draw itself
                        forms[form.objgen] = stream(form, res)
                    if forms[form.objgen]:
                        drawn()
        for i in marked:
            operands = items[i].operands
            props = operands[1] if len(operands) > 1 else None
            if isinstance(props, Name) and isinstance(res, Dictionary):  # a property list named in the resources
                props = (res.get("/Properties") or Dictionary()).get(str(props))
            if isinstance(props, Dictionary):
                props.ActualText = String("")
            else:
                items[i] = pikepdf.ContentStreamInstruction([Name.Artifact, Dictionary(ActualText=String(""))],
                                                            pikepdf.Operator("BDC"))
            done += 1
        if marked:
            data = pikepdf.unparse_content_stream(items)
            if isinstance(holder, pikepdf.Stream):
                holder.write(data)
            else:
                holder.Contents = pdf.make_stream(data)
        return loose

    for page in pdf.pages:
        stream(page.obj, page.resources)
    return done


def _structure_destinations(pdf: pikepdf.Pdf, elems, dropped: list | None = None) -> int:
    """Each destination inside the document (a link's, a bookmark's, a GoTo action's) also names a structure element
    on its page: the first heading there, else the first tag with content there (PDF/UA-2 8.8). One that goes to a
    place the document doesn't have (a name it never defines, a page no longer in it) is dropped: it went nowhere,
    and 8.8 fails on it (#615). dropped, if given, gets each: (the page its link is on, from 1, or None; the name or
    "a page")."""
    page_of = _page_index(pdf)
    first: dict[int, object] = {}
    heading: dict[int, object] = {}
    for e in elems:
        content = _content_of(page_of, e, forms=True)  # a page drawn as a form still has its tags (#631)
        if not content:
            continue
        p = content[0][0]
        first.setdefault(p, e)
        if str(e.get("/S", "")).startswith("/H"):
            heading.setdefault(p, e)
    named = _named_destinations(pdf)

    def explicit(d):
        if isinstance(d, Name):  # a key of the catalog's /Dests, without its slash (#615)
            d = named.get(str(d)[1:])
        elif isinstance(d, String):
            d = named.get(str(d))
        if isinstance(d, Dictionary):
            d = d.get("/D")
        return d if isinstance(d, Array) and len(d) and isinstance(d[0], Dictionary) else None

    def dead(d) -> bool:
        """A destination inside the document that goes nowhere."""
        if isinstance(d, Name):
            return str(d)[1:] not in named
        if isinstance(d, String):
            return str(d) not in named
        d = explicit(d)
        return d is not None and d[0].objgen not in page_of

    def drop(d, where) -> None:
        nonlocal gone
        gone += 1
        if dropped is not None:
            dropped.append((where, str(d)[1:] if isinstance(d, Name) else str(d) if isinstance(d, String) else "a page"))

    def structured(d) -> Array | None:
        d = explicit(d)
        p = page_of.get(d[0].objgen) if d is not None else None
        target = heading.get(p) or first.get(p) if p is not None else None
        return Array([target, *list(d)[1:]]) if target is not None else None

    done = gone = 0

    def fix(holder, where=None) -> None:
        """A link annotation or outline item: its /Dest becomes a GoTo with /SD, or its GoTo action gains /SD; one
        that goes nowhere loses it."""
        nonlocal done
        action = holder.get("/A")
        if "/Dest" in holder:
            if dead(holder.Dest):
                drop(holder.Dest, where)
                del holder["/Dest"]
                return
            sd = structured(holder.Dest)
            if sd is not None:
                holder.A = Dictionary(S=Name.GoTo, D=holder.Dest, SD=sd)
                del holder["/Dest"]
                done += 1
        elif isinstance(action, Dictionary) and action.get("/S") == Name.GoTo and "/SD" not in action:
            if dead(action.get("/D")):
                drop(action.get("/D"), where)
                del holder["/A"]
                return
            sd = structured(action.get("/D"))
            if sd is not None:
                action.SD = sd
                done += 1

    for n, page in enumerate(pdf.pages, 1):
        for annot in page.obj.get("/Annots") or []:
            if isinstance(annot, Dictionary) and annot.get("/Subtype") == Name.Link:
                fix(annot, n)
    opening = pdf.Root.get("/OpenAction")
    if isinstance(opening, Array):
        sd = structured(opening)
        if sd is not None:
            pdf.Root.OpenAction = Dictionary(S=Name.GoTo, D=opening, SD=sd)
            done += 1
    elif isinstance(opening, Dictionary) and opening.get("/S") == Name.GoTo and "/SD" not in opening:
        sd = structured(opening.get("/D"))
        if sd is not None:
            opening.SD = sd
            done += 1
    outlines = pdf.Root.get("/Outlines")
    stack = [outlines.get("/First")] if isinstance(outlines, Dictionary) else []
    seen = set()
    while stack:
        item = stack.pop()
        if not isinstance(item, Dictionary) or item.objgen in seen:
            continue
        seen.add(item.objgen)
        fix(item)
        stack += [item.get("/Next"), item.get("/First")]
    return done


def _dead_links(dead: list[tuple]) -> str:
    """The report's line for links and bookmarks that went to places the document doesn't have, for a person to fix
    in the source: how many, the names they used, the pages they're on."""
    names = Counter(name for _, name in dead)
    pages = sorted({p for p, _ in dead if p is not None})
    said = ", ".join(f"{n} ({c})" if c > 1 else n for n, c in names.most_common(6)) + (", …" if len(names) > 6 else "")
    on = ", ".join(map(str, pages[:12])) + (", …" if len(pages) > 12 else "")
    return (f"{len(dead)} link(s) or bookmark(s) went to places the document doesn't have ({said}) and now go "
            f"nowhere{f', on page(s) {on}' if on else ''}: fix them in the source document")


def _named_destinations(pdf: pikepdf.Pdf) -> dict[str, object]:
    out: dict[str, object] = {}
    dests = pdf.Root.get("/Dests")
    if isinstance(dests, Dictionary):
        out.update({str(k)[1:]: v for k, v in dests.items()})
    names = pdf.Root.get("/Names")
    if isinstance(names, Dictionary) and "/Dests" in names:
        try:
            out.update({str(k): v for k, v in pikepdf.NameTree(names.Dests).items()})
        except (pikepdf.PdfError, TypeError, ValueError):
            pass
    return out


def _describe_fields(pdf: pikepdf.Pdf) -> int:
    """A form field's widget with no Contents gets one: the field's tooltip, else its name."""
    done = 0
    for page in pdf.pages:
        for annot in page.obj.get("/Annots") or []:
            if not isinstance(annot, Dictionary) or annot.get("/Subtype") != Name.Widget or "/Contents" in annot:
                continue
            field, said = annot, None
            while isinstance(field, Dictionary) and said is None:
                said = field.get("/TU") or field.get("/T")
                field = field.get("/Parent")
            if said is not None and str(said).strip():
                annot.Contents = String(str(said))
                done += 1
    return done


def _describe_files(pdf: pikepdf.Pdf) -> int:
    """Each embedded file's specification without a Desc gets its file name as one."""
    names = pdf.Root.get("/Names")
    if not isinstance(names, Dictionary) or "/EmbeddedFiles" not in names:
        return 0
    done = 0
    try:
        for _, spec in pikepdf.NameTree(names.EmbeddedFiles).items():
            if isinstance(spec, Dictionary) and "/Desc" not in spec:
                spec.Desc = String(str(spec.get("/UF") or spec.get("/F") or "attached file"))
                done += 1
    except (pikepdf.PdfError, TypeError, ValueError):
        return done
    return done
