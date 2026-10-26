"""#403: MathML a document's own tags carry, as LaTeX's tagging writes it: an associated MathML file on the Formula,
or MathML structure elements under it (#421: rebuilt from the elements, their text from the page). Kept as written,
and read by rule for a Formula with no read-aloud text."""
import pikepdf
import pymupdf as fitz
import pytest
from pikepdf import Array, Dictionary, Name, String

from app.pipeline import mathml
from app.pipeline.analyzer import analyze_document
from app.tagging.mathml_tags import MATHML_NS, formula_mathml, has_mathml, in_mathml
from app.tagging.tagger import tag_document
from app.verification.integrity import check_integrity
from tests.engine.test_mathml import fake_mathcat

QUADRATIC = ('<math xmlns="http://www.w3.org/1998/Math/MathML" display="block"> <mi>x</mi> <mo>=</mo> '
             '<mfrac> <mn>1</mn> <mn>2</mn> </mfrac> </math>')


def _file(pdf, data: bytes, name: str, subtype: str, rel: str):
    stream = pdf.make_stream(data)
    stream.Type, stream.Subtype = Name.EmbeddedFile, Name("/" + subtype)
    return pdf.make_indirect(Dictionary(Type=Name.Filespec, F=String(name), UF=String(name),
                                        EF=Dictionary(F=stream, UF=stream), AFRelationship=Name(rel)))


def latex_tagged(path, tmp):
    """Two formulas tagged as LaTeX's tagging does: one with its MathML (and LaTeX) as associated files and its
    glyphs marked on the Formula itself; one with MathML structure elements under it, and no page of its own."""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "The roots of the equation are given below.", fontsize=12)
    page.insert_text((200, 150), "x = 1/2", fontsize=12)
    page.insert_text((200, 200), "y = 3", fontsize=12)
    doc.save(tmp)
    with pikepdf.open(tmp) as pdf:
        pg = pdf.pages[0]
        out, bt, mcid = [], 0, 0
        for ins in pikepdf.parse_content_stream(pg.obj):
            op = str(ins.operator)
            if op == "BT":
                out.append(pikepdf.ContentStreamInstruction([Name.Span, Dictionary(MCID=mcid)],
                                                            pikepdf.Operator("BDC")))
            out.append(ins)
            if op == "ET":
                out.append(pikepdf.ContentStreamInstruction([], pikepdf.Operator("EMC")))
                bt, mcid = bt + 1, mcid + 1
        pg.obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(out))
        ns = pdf.make_indirect(Dictionary(Type=Name.Namespace, NS=String(MATHML_NS)))
        root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot, Namespaces=Array([ns])))
        docel = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Document, P=root))
        para = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.P, P=docel, Pg=pg.obj, K=0))
        af = Array([_file(pdf, QUADRATIC.encode(), "mathml-1.xml", "application#2Fmathml+xml", "/Supplement"),
                    _file(pdf, rb"x = \frac{1}{2}", "f1.tex", "application#2Fx-tex", "/Source")])
        f1 = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Formula, P=docel, Pg=pg.obj, K=1, AF=af))
        f2 = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Formula, P=docel))
        math = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name("/math"), NS=ns, P=f2))
        mi = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name("/mi"), NS=ns, P=math, Pg=pg.obj, K=2))
        math.K, f2.K = Array([mi]), Array([math])
        docel.K = Array([para, f1, f2])
        root.K = Array([docel])
        root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array([0, Array([para, f1, mi])])))
        root.ParentTreeNextKey = 1
        pg.obj.StructParents = 0
        pdf.Root.StructTreeRoot = root
        pdf.Root.MarkInfo = Dictionary(Marked=True)
        pdf.save(path)
    return path


def _formulas(pdf):
    found = []
    stack = [pdf.Root.StructTreeRoot]
    while stack:
        e = stack.pop()
        if isinstance(e, Dictionary):
            if e.get("/S") == Name.Formula:
                found.append(e)
            k = e.get("/K")
            stack += list(k) if isinstance(k, Array) else [k] if isinstance(k, Dictionary) else []
    return found


def test_a_formulas_own_mathml_is_found(tmp_path):
    with pikepdf.open(latex_tagged(tmp_path / "in.pdf", tmp_path / "raw.pdf")) as pdf:
        with_file, with_elements = sorted(_formulas(pdf), key=lambda f: "/AF" not in f)
        assert formula_mathml(with_file) == (QUADRATIC, r"x = \frac{1}{2}")
        assert formula_mathml(with_elements) == (None, None)
        assert has_mathml(with_file) and has_mathml(with_elements, list(with_elements.K))
        assert in_mathml(with_elements.K[0]) and not in_mathml(with_file)


def test_a_documents_mathml_is_cleaned_like_ours():
    m = mathml.clean(QUADRATIC, r"x = \frac{1}{2}")
    assert m.startswith('<math xmlns="http://www.w3.org/1998/Math/MathML" display="block"><semantics><mrow><mi>x')
    assert m.endswith(r'<annotation encoding="application/x-tex">x = \frac{1}{2}</annotation></semantics></math>')
    assert mathml.clean("<math><mi>y</mi></math>").startswith(f'<math xmlns="{MATHML_NS}">')  # no namespace given
    assert mathml.clean(f'<math xmlns="{MATHML_NS}"><mi>x</mi><script>1</script></math>') is None
    assert mathml.clean("<math><mi>x</math>") is None and mathml.clean("") is None


def test_kept_formulas_are_read_from_their_mathml_and_their_mathml_is_kept(tmp_path, monkeypatch):
    monkeypatch.setenv("MATHCAT_BIN", fake_mathcat(tmp_path, "x is equal to 1 half"))
    src = latex_tagged(tmp_path / "in.pdf", tmp_path / "raw.pdf")
    out = tmp_path / "out.pdf"
    result = tag_document(src, out, analyze_document(src), title="t", language="en-US")
    assert result.existing_tags["usable"]
    assert result.formulas_read_from_mathml == 2  # the one whose MathML is a file, and the one of elements (#421)
    doc, elements = sorted(result.document_formulas, key=lambda d: d["bbox"][1])
    assert (doc["page"], doc["source"], doc["spoken"]) == (0, "document", "x is equal to 1 half")
    assert doc["mathml"].startswith("<math") and doc["rule"]["nemeth"]
    # #421: the other's MathML, rebuilt from its elements with their text from the page, is read and listed too
    assert (elements["page"], elements["source"], elements["spoken"]) == (0, "document", "x is equal to 1 half")
    assert "<mi>y = 3</mi>" in elements["mathml"] and elements["rule"]["nemeth"]
    with pikepdf.open(out) as pdf:
        formulas = _formulas(pdf)
        assert len(formulas) == 2  # neither became a Div
        with_file = next(f for f in formulas if "/AF" in f)
        assert str(with_file.Alt) == "x is equal to 1 half" and formula_mathml(with_file)[0] == QUADRATIC
        with_elements = next(f for f in formulas if "/AF" not in f)
        assert in_mathml(with_elements.K[0]) and in_mathml(with_elements.K[0].K[0])
        assert str(with_elements.Alt) == "x is equal to 1 half"  # it had no read-aloud text: now its MathML's
        rolemap = pdf.Root.StructTreeRoot.get("/RoleMap") or {}
        assert not {"/math", "/mi"} & {str(k) for k in rolemap.keys()}  # their namespace says what they are
        errors, _ = check_integrity(pdf)
        assert not [e for e in errors if e.severity == "error"]


def test_an_ai_reading_of_the_same_formula_gives_way_to_the_documents_mathml():
    ai = {"page": 0, "bbox": [100, 100, 200, 120], "spoken": "x equals a half", "latex": "x=1/2", "mathml": "<m/>",
          "source": "text", "name": "page-001_formula-1.mml"}
    other = {**ai, "bbox": [100, 400, 200, 420], "name": "page-001_formula-2.mml"}
    notation = [{"page": 0, "bbox": [100, 100, 200, 120], "spoken": "x equals a half"},
                {"page": 0, "bbox": [100, 400, 200, 420], "spoken": "other"}]
    formulas = [ai, other]
    doc = {"page": 0, "bbox": [102, 101, 199, 121], "source": "document", "mathml": "<m/>", "spoken": "x is 1 half"}
    assert mathml.add_document_formulas(formulas, notation, [doc]) == 1
    assert [f["name"] for f in formulas] == ["page-001_tagged-1.mml", "page-001_formula-2.mml"]
    assert notation == [{"page": 0, "bbox": [100, 400, 200, 420], "spoken": "other"}]


def test_mathml_is_rebuilt_from_its_elements():
    """#421: names from the roles, nesting from the tree, attributes from /A, each leaf's text from its marked content
    (or its ActualText); with no math element on top, one is put around them."""
    from app.tagging.mathml_tags import mathml_from_elements
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pg = pdf.pages[0].obj
    ns = Dictionary(Type=Name.Namespace, NS=String(MATHML_NS))

    def el(role, k, **extra):
        return Dictionary(Type=Name.StructElem, S=Name("/" + role), NS=ns, K=k, **extra)
    frac = el("mfrac", Array([el("mn", 1), el("mn", Dictionary(Type=Name.MCR, Pg=pg, MCID=2))]))
    math = el("math", Array([el("mi", 0), el("mo", Array(), ActualText=String("=")), frac]),
              A=Dictionary(O=Name("/MathML-3"), display=String("block")))
    formula = Dictionary(Type=Name.StructElem, S=Name.Formula, Pg=pg, K=Array([math]))
    text = {0: "x", 1: "1", 2: "2"}
    got = mathml_from_elements(formula, lambda page, mcid: text[mcid] if page.objgen == pg.objgen else "?")
    assert got == (f'<math xmlns="{MATHML_NS}" display="block"><mi>x</mi><mo>=</mo>'
                   '<mfrac><mn>1</mn><mn>2</mn></mfrac></math>')
    assert mathml.clean(got) is not None
    bare = Dictionary(Type=Name.StructElem, S=Name.Formula, Pg=pg, K=Array([el("mi", 0)]))
    assert mathml_from_elements(bare, lambda page, mcid: "y") == f'<math xmlns="{MATHML_NS}"><mi>y</mi></math>'
    assert mathml_from_elements(Dictionary(S=Name.Formula, K=0), lambda page, mcid: "") is None
