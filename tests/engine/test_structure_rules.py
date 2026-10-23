"""#437, #438: tags at the right level, and figures that say where they are, as PAC checks (veraPDF's PDF/UA-1 profile
doesn't): a nested Document, a P in a P, inline tags straight in a grouping tag, figures as blocks, bounding boxes."""
import pikepdf
from pikepdf import Array, Dictionary, Name

from app.tagging import structure_rules
from app.tagging.structure_rules import _user_bbox


def _tree(spec):
    """A PDF whose tag tree is spec: (role, [children]) tuples, an int being a piece of marked content."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[0].obj
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    pdf.Root.StructTreeRoot = root

    def make(node, parent):
        role, kids = node
        e = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name("/" + role), P=parent, Pg=page))
        e.K = Array([k if isinstance(k, int) else make(k, e) for k in kids])
        return e

    root.K = make(spec, root)
    return pdf


def _shape(e):
    kids = e.get("/K")
    kids = list(kids) if isinstance(kids, Array) else []
    return (str(e.S)[1:], [k if isinstance(k, int) else _shape(k) for k in kids])


def test_a_nested_document_becomes_a_part_and_a_p_holding_ps_a_div():
    pdf = _tree(("Document", [("Document", [("P", [0])]), ("P", [("P", [1]), ("P", [2])]), ("P", [3, ("P", [4])])]))
    done = structure_rules.apply(pdf)
    assert _shape(pdf.Root.StructTreeRoot.K) == (
        "Document", [("Part", [("P", [0])]), ("Div", [("P", [1]), ("P", [2])]), ("P", [3, ("Span", [4])])])
    assert (done["documents_made_parts"], done["paragraphs_made_groups"], done["paragraphs_made_spans"]) == (1, 1, 1)



def test_a_part_inside_a_part_becomes_a_sect():
    """A deck in a longer document: its Document made a Part, and its slides (Slide, mapped to Part) Sects in it."""
    pdf = _tree(("Document", [("Part", [("P", [0])]), ("Document", [("Slide", [("P", [1])]), ("Part", [("P", [2])])]),
                              ("Sect", [("Part", [("P", [3])])])]))
    pdf.Root.StructTreeRoot.RoleMap = Dictionary(Slide=Name.Part)
    done = structure_rules.apply(pdf)
    assert _shape(pdf.Root.StructTreeRoot.K) == (
        "Document", [("Part", [("P", [0])]), ("Part", [("Sect", [("P", [1])]), ("Sect", [("P", [2])])]),
                     ("Sect", [("Sect", [("P", [3])])])])
    assert (done["documents_made_parts"], done["parts_made_sections"]) == (1, 3)

def test_inline_tags_straight_in_a_grouping_tag_are_put_in_a_paragraph_a_run_together():
    pdf = _tree(("Document", [("Sect", [("Span", [0]), ("Link", [1]), ("Span", [2]), ("P", [3]), ("Note", [4])])]))
    done = structure_rules.apply(pdf)
    assert _shape(pdf.Root.StructTreeRoot.K) == (
        "Document", [("Sect", [("P", [("Span", [0]), ("Link", [1]), ("Span", [2])]), ("P", [3]),
                               ("P", [("Note", [4])])])])
    assert done["inline_put_in_paragraphs"] == 4
    sect = pdf.Root.StructTreeRoot.K.K[0]
    para = sect.K[0]
    assert para.P == sect and all(k.P == para for k in para.K)  # the parents point the right way
    assert para.Pg == pdf.pages[0].obj


def test_a_figure_standing_as_a_block_says_so_and_where_it_is():
    pdf = _tree(("Document", [("Sect", [("Figure", [0]), ("P", [1, ("Figure", [2])])])]))
    sect = pdf.Root.StructTreeRoot.K.K[0]
    block, inline = sect.K[0], sect.K[1].K[1]
    # The page in PyMuPDF's space: y down, so user space is y = 792 - y
    flip = (1.0, 0.0, 0.0, -1.0, 0.0, 792.0)
    done = structure_rules.apply(pdf, {block.objgen: (0, (100, 100, 300, 200)),
                                       inline.objgen: (0, (50, 700, 60, 710))}, [flip])
    assert block.A.Placement == Name.Block and list(block.A.BBox) == [100, 592, 300, 692]
    assert "/Placement" not in inline.A and list(inline.A.BBox) == [50, 82, 60, 92]  # inline in its paragraph
    assert (done["figures_placed_as_blocks"], done["figures_given_bbox"]) == (1, 2)
    # Attributes it already has are kept, the layout ones beside them; its own box isn't replaced
    pdf = _tree(("Document", [("Figure", [0])]))
    fig = pdf.Root.StructTreeRoot.K.K[0]
    fig.A = Array([Dictionary(O=Name("/Table"), Summary="x"), Dictionary(O=Name.Layout, BBox=Array([1, 2, 3, 4]))])
    structure_rules.apply(pdf, {fig.objgen: (0, (100, 100, 300, 200))}, [flip])
    assert len(fig.A) == 2 and list(fig.A[1].BBox) == [1, 2, 3, 4] and fig.A[1].Placement == Name.Block


def test_a_box_goes_back_to_user_space_on_a_rotated_or_offset_page():
    # Rotated 90 degrees, as PyMuPDF maps a 612 x 792 page: (x, y) -> (792 - y, x)
    rotated = (0.0, 1.0, -1.0, 0.0, 792.0, 0.0)
    assert _user_bbox(rotated, (100, 50, 300, 150)) == [50, 492, 150, 692]
    # A MediaBox that doesn't start at 0: the matrix's offset is taken off
    assert _user_bbox((1.0, 0.0, 0.0, -1.0, -20.0, 812.0), (0, 0, 100, 100)) == [20, 712, 120, 812]
    # Numbers from numpy (a music or scan figure's box) come out as plain ones the file can hold
    import numpy as np
    out = _user_bbox((1.0, 0.0, 0.0, -1.0, 0.0, 792.0), tuple(np.float64(v) for v in (100, 100, 300, 200)))
    assert out == [100, 592, 300, 692] and all(type(v) is float for v in out)


def test_roles_go_through_the_role_map():
    pdf = _tree(("Document", [("Sect", [("MyNote", [0])])]))
    pdf.Root.StructTreeRoot.RoleMap = Dictionary(MyNote=Name.Note)
    structure_rules.apply(pdf)
    assert _shape(pdf.Root.StructTreeRoot.K) == ("Document", [("Sect", [("P", [("MyNote", [0])])])])


def test_a_table_body_with_no_head_gets_its_header_rows_as_a_head_or_goes():
    # Header cells in the first row: that row becomes a THead before the body
    row = lambda cell, n: ("TR", [(cell, [n]), (cell, [n + 1])])  # noqa: E731
    pdf = _tree(("Document", [("Table", [("TBody", [row("TH", 0), row("TD", 2), row("TD", 4)])])]))
    done = structure_rules.apply(pdf)
    assert _shape(pdf.Root.StructTreeRoot.K) == (
        "Document", [("Table", [("THead", [row("TH", 0)]), ("TBody", [row("TD", 2), row("TD", 4)])])])
    table = pdf.Root.StructTreeRoot.K.K[0]
    assert table.K[0].P == table and table.K[0].K[0].P == table.K[0]
    assert done["header_rows_made_thead"] == 1
    # No header row (or a first column of headers only): the rows stand in the table itself
    side = ("TR", [("TH", [0]), ("TD", [1])])
    pdf = _tree(("Document", [("Table", [("Caption", [9]), ("TBody", [side, row("TD", 2)])])]))
    done = structure_rules.apply(pdf)
    assert _shape(pdf.Root.StructTreeRoot.K) == ("Document", [("Table", [("Caption", [9]), side, row("TD", 2)])])
    table = pdf.Root.StructTreeRoot.K.K[0]
    assert all(r.P == table for r in list(table.K)[1:])
    assert done["table_bodies_unwrapped"] == 1
    # A table of header rows only has no body to set them apart from: its rows stand in the table too
    pdf = _tree(("Document", [("Table", [("TBody", [row("TH", 0)])])]))
    structure_rules.apply(pdf)
    assert _shape(pdf.Root.StructTreeRoot.K.K[0]) == ("Table", [row("TH", 0)])
    # A table that has a head already is left as it is
    spec = ("Table", [("THead", [row("TH", 0)]), ("TBody", [row("TD", 2)])])
    pdf = _tree(("Document", [spec]))
    structure_rules.apply(pdf)
    assert _shape(pdf.Root.StructTreeRoot.K.K[0]) == spec
