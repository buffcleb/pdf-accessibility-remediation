"""#619: a list says how its items are numbered (ListNumbering) by the same rule that nests it (lists.label_kind),
which tells a letter from a roman numeral by the labels around it."""
import pikepdf
import pymupdf as fitz
from pikepdf import Name

from app.pipeline.lists import LabelKey
from app.pipeline.orchestrator import prepare_analysis
from app.tagging.struct_tree import _list_numbering, _numbering
from app.tagging.tagger import tag_document


def test_each_kind_of_label_has_its_numbering():
    assert [_numbering(LabelKey(k, ".")) for k in ("decimal", "lower", "upper", "lroman", "uroman")] == [
        "Decimal", "LowerAlpha", "UpperAlpha", "LowerRoman", "UpperRoman"]
    assert _numbering(LabelKey("bullet", "•")) == "Disc" and _numbering(LabelKey("bullet", "–")) == "None"
    # A first label alone: "i" is roman, but a single c, l, v or x is a letter, as label_kind reads it
    assert [_list_numbering(x) for x in ("(i)", "ii.", "(c)", "v.", "C.", "IV.", "3.", None)] == [
        "LowerRoman", "LowerRoman", "LowerAlpha", "LowerAlpha", "UpperAlpha", "UpperRoman", "Decimal", "Disc"]


def _numberings(tmp_path, lines, name="list"):
    """The ListNumbering of each list tagged on a page of these lines ("" leaves a gap)."""
    doc = fitz.open()
    page = doc.new_page()
    y = 90
    for line in lines:
        if line:
            page.insert_text((72, y), line, fontsize=11)
        y += 18
    src, out = tmp_path / f"{name}.pdf", tmp_path / f"{name}_tagged.pdf"
    doc.save(src)
    tag_document(src, out, prepare_analysis(src), title="Lists", language="en-US")
    with pikepdf.open(out) as pdf:
        found, stack = [], [pdf.Root.StructTreeRoot]
        while stack:
            e = stack.pop()
            k = e.get("/K")
            for c in (list(k) if isinstance(k, pikepdf.Array) else [k]):
                if isinstance(c, pikepdf.Dictionary) and "/S" in c:
                    if c.S == Name.L:
                        found.append(str(c.A.ListNumbering))
                    stack.append(c)
        return found


def test_a_lettered_list_starting_at_c_is_lettered(tmp_path):
    """Before #619 a list whose first label was "(c)" (a lettered list carried on after a figure) was LowerRoman."""
    assert _numberings(tmp_path, ["Some text before the list, as a paragraph.", "(c) Carrots and onions",
                                  "(d) Dates and figs", "(e) Eggs and flour"]) == ["/LowerAlpha"]


def test_a_roman_list_is_still_roman(tmp_path):
    assert _numberings(tmp_path, ["Some text before the list, as a paragraph.", "(i) The first part",
                                  "(ii) The second part", "(iii) The third part"]) == ["/LowerRoman"]


def test_a_list_split_in_two_reads_its_first_label_after_the_list_before(tmp_path):
    """A roman list carried on after a paragraph starts "(v)": after "(iv)" it is roman. A lettered one carried on at
    "(c)", after "(b)", stays lettered."""
    roman = ["(i) The first part", "(ii) The second part", "(iii) The third part", "(iv) The fourth part", "",
             "A paragraph between the parts of the list.", "", "(v) The fifth part", "(vi) The sixth part"]
    assert _numberings(tmp_path, roman, "roman") == ["/LowerRoman", "/LowerRoman"]
    letters = ["(a) Apples and pears", "(b) Bread and butter", "", "A paragraph between the parts of the list.", "",
               "(c) Carrots and onions", "(d) Dates and figs"]
    assert _numberings(tmp_path, letters, "letters") == ["/LowerAlpha", "/LowerAlpha"]


def test_a_roman_list_nested_in_a_lettered_one_is_roman(tmp_path):
    found = _numberings(tmp_path, ["(a) Apples and pears", "(b) Bread and butter", "        (i) white bread",
                                   "        (ii) brown bread", "(c) Carrots and onions"])
    assert "/LowerRoman" in found and "/LowerAlpha" in found
