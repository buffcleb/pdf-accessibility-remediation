"""#403: each formula's MathML attached to the tag carrying its reading, in a PDF 2.0 file (PDF/UA-2)."""
import pikepdf
from pikepdf import Array, Dictionary, Name, String

from app.tagging.mathml_embed import embed
from app.tagging.mathml_tags import formula_mathml
from app.verification.tagmap import build_tagmap
from tests.engine.test_mathml_tags import QUADRATIC, latex_tagged

M = '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>y</mi><mo>=</mo><mn>3</mn></math>'


def _doc(tmp_path, version):
    """The LaTeX-like document, its first formula's MathML file taken off, the paragraph given read-aloud text."""
    src = latex_tagged(tmp_path / "t.pdf", tmp_path / "raw.pdf")
    out = tmp_path / f"in{version}.pdf"
    with pikepdf.open(src) as pdf:
        doc = pdf.Root.StructTreeRoot.K[0]
        para, f1, _ = list(doc.K)
        del f1["/AF"]
        para.ActualText = String("the roots are given below")
        pdf.save(out, min_version=version)
    return out


def _boxes(path):
    m = build_tagmap(path)
    return {e["i"]: (e["page"] - 1, e["boxes"][0]["bbox"]) for e in m["elements"] if e.get("boxes")}, m


def test_a_pdf_1_file_gets_no_associated_files(tmp_path):
    path = _doc(tmp_path, "1.7")
    before = path.read_bytes()
    assert embed(path, [{"page": 0, "bbox": [0, 0, 600, 800], "mathml": M}]) == 0
    assert path.read_bytes() == before


def test_formulas_get_their_mathml_on_the_tag_carrying_their_reading(tmp_path):
    path = _doc(tmp_path, "2.0")
    boxes, m = _boxes(path)
    roles = {e["i"]: e["role"] for e in m["elements"]}
    formula = next(i for i, r in roles.items() if r == "Formula" and boxes.get(i) and boxes[i][1][1] < 160)
    para = next(i for i, r in roles.items() if r == "P")
    formulas = [{"page": boxes[formula][0], "bbox": boxes[formula][1], "mathml": QUADRATIC},
                {"page": boxes[para][0], "bbox": boxes[para][1], "mathml": M},
                {"page": 0, "bbox": [500, 700, 510, 710], "mathml": M}]  # nothing there
    assert embed(path, formulas) == 2
    with pikepdf.open(path) as pdf:
        doc = pdf.Root.StructTreeRoot.K[0]
        p, f1, f2 = list(doc.K)
        assert formula_mathml(f1)[0] == QUADRATIC and formula_mathml(p)[0] == M
        spec = f1.AF[0]
        assert (spec.AFRelationship, spec.UF, spec.EF.F.Subtype) == (Name.Supplement, "mathml-1.xml",
                                                                     Name("/application/mathml+xml"))
        assert "/AF" not in f2  # its MathML is its elements: not touched
    # Again: what has MathML keeps it, nothing doubles
    assert embed(path, formulas) == 0


def test_a_sentence_without_read_aloud_text_gets_no_mathml(tmp_path):
    path = _doc(tmp_path, "2.0")
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        del pdf.Root.StructTreeRoot.K[0].K[0]["/ActualText"]
        pdf.save(path)
    boxes, m = _boxes(path)
    para = next(e["i"] for e in m["elements"] if e["role"] == "P")
    assert embed(path, [{"page": boxes[para][0], "bbox": boxes[para][1], "mathml": M}]) == 0
