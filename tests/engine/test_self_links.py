"""#484: links that point to their own place go (Word's caption bookmarks: PAC warned of a possibly inappropriate Link
in a Caption, and following one does nothing). Their annotation, structure reference and parent-tree entry go; an
empty Link tag goes, and one holding words gives them back to its parent. Links that go somewhere stay."""
from __future__ import annotations

import pikepdf
from pikepdf import Array, Dictionary, Name, String

from app.tagging.self_links import drop_self_links

RECT = [46, 665, 87, 688]


def _doc(dests: list, words_in_link: bool = False):
    """Two pages; on the first a Caption holding words (MCID 0) and one Link per destination, each with its OBJR."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[0].obj
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    pdf.Root.StructTreeRoot = root
    cap = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Caption, P=root, Pg=page, K=Array([0])))
    root.K = cap
    annots, parents = [], {}
    for n, dest in enumerate(dests):
        link = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Link, P=cap, Pg=page))
        annot = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link, Rect=Array(RECT), StructParent=n,
                                             **dest(pdf)))
        objr = Dictionary(Type=Name.OBJR, Obj=annot, Pg=page)
        link.K = Array([1 + n, objr]) if words_in_link else Array([objr])
        cap.K.append(link)
        annots.append(annot)
        parents[n] = link
    page.Annots = Array(annots)
    root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array([x for n, e in parents.items() for x in (n, e)])))
    return pdf, cap


def _to(page_index, top):
    return lambda pdf: {"Dest": Array([pdf.pages[page_index].obj, Name.XYZ, 46, top, 0])}


def _roles(e):
    return [str(k.get("/S") or k.get("/Type")) if isinstance(k, Dictionary) else k for k in e.K]


def test_a_link_to_itself_goes_and_one_elsewhere_stays():
    pdf, cap = _doc([_to(0, 688), _to(1, 700), _to(0, 400)])
    assert drop_self_links(pdf) == 1
    assert _roles(cap) == [0, "/Link", "/Link"]
    assert [float(a.Rect[3]) for a in pdf.pages[0].obj.Annots] == [688, 688]
    nums = pikepdf.NumberTree(pdf.Root.StructTreeRoot.ParentTree)
    assert 0 not in nums and 1 in nums and 2 in nums
    assert drop_self_links(pdf) == 0


def test_a_link_holding_words_gives_them_back():
    pdf, cap = _doc([_to(0, 665)], words_in_link=True)  # the destination at the box's lower edge: still itself
    page = pdf.pages[0].obj
    page.StructParents = 9  # the page's marked content: MCID 0 the caption's, MCID 1 the link's
    link = cap.K[1]
    nums = pdf.Root.StructTreeRoot.ParentTree.Nums
    nums.extend([9, Array([cap, link])])
    assert drop_self_links(pdf) == 1
    assert _roles(cap) == [0, 1]
    assert "/Annots" in pdf.pages[0].obj and len(pdf.pages[0].obj.Annots) == 0
    # The parent tree says the words are the caption's now, not the removed Link's (an integrity check found it)
    seq = pikepdf.NumberTree(pdf.Root.StructTreeRoot.ParentTree)[9]
    assert [x.objgen for x in seq] == [cap.objgen, cap.objgen]


def test_named_and_action_destinations_are_followed():
    def named(pdf):
        pdf.Root.Names = Dictionary(Dests=pdf.make_indirect(Dictionary(Names=Array([
            String("_Ref5"), Array([pdf.pages[0].obj, Name.XYZ, 40, 690, 0])]))))
        return {"Dest": String("_Ref5")}

    def action(pdf):
        return {"A": Dictionary(S=Name.GoTo, D=Array([pdf.pages[0].obj, Name.FitH, 680]))}

    def uri(pdf):
        return {"A": Dictionary(S=Name.URI, URI=String("https://example.org"))}

    pdf, cap = _doc([named, action, uri])
    assert drop_self_links(pdf) == 2
    assert _roles(cap) == [0, "/Link"]
