"""#403: formulas the AI read with LaTeX are kept as MathML too, and a document with formulas gets the math files
(one .mml per formula and a formula list a screen reader can move through)."""
import asyncio
import sys
import zipfile
import xml.etree.ElementTree as ET

import pytest

from app.pipeline import mathml
from app.pipeline.models import ImageRegion

NS = "{http://www.w3.org/1998/Math/MathML}"


def test_latex_becomes_mathml_that_keeps_its_source():
    m = mathml.to_mathml(r"\frac{a+b}{2}", display=True)
    root = ET.fromstring(m)
    assert root.tag == f"{NS}math" and root.get("display") == "block"
    assert root.find(f"{NS}semantics/{NS}mrow/{NS}mfrac") is not None
    assert root.find(f"{NS}semantics/{NS}annotation").text == r"\frac{a+b}{2}"
    assert ET.fromstring(mathml.to_mathml(r"\sqrt[3]{x} \leq \int_0^1 f(x)\,dx")).get("display") == "inline"


@pytest.mark.parametrize("latex", ["", None, r"\frac{a", "x" * 5000])
def test_latex_that_cant_make_good_mathml_gets_none(latex):
    assert mathml.to_mathml(latex) is None


def test_the_files_are_inert_whatever_the_latex_says():
    """Text from the document is escaped; links, styles and colors are dropped."""
    m = mathml.to_mathml(r"\text{<script>alert(1)</script> & co} + \href{javascript:x}{y} + \style{color:red}{z}"
                         r" + \color{red}{w}")
    root = ET.fromstring(m)
    names = {el.tag.removeprefix(NS) for el in root.iter()}
    assert "script" not in names and names <= mathml.ELEMENTS
    assert all(set(el.attrib) <= mathml.ATTRIBUTES for el in root.iter())
    assert not any("javascript" in v for el in root.iter() for v in el.attrib.values()) and "<script" not in m
    assert root.find(f".//{NS}mtext").text.startswith("<script>")  # shown as the text it is


def _formula_image(page, y, latex, alt="a formula", decorative=False):
    return ImageRegion(id=f"i{page}{y}", page=page, bbox=(100, y, 300, y + 40), xref=1, alt=alt, kind="formula",
                       latex=latex, decorative=decorative)


def test_formulas_are_collected_in_page_order_and_named_by_place():
    notation = [
        {"page": 1, "bbox": [72, 300, 400, 320], "role": "Formula", "spoken": "x squared", "latex": "x^2"},
        {"page": 0, "bbox": [72, 500, 400, 520], "role": "P", "spoken": "y", "latex": "y"},
        {"page": 0, "bbox": [72, 100, 400, 120], "role": "P", "spoken": "dose by rule", "latex": None},
        {"page": 1, "bbox": [72, 100, 400, 120], "role": "Formula", "spoken": "broken", "latex": r"\frac{a"},
    ]
    images = [_formula_image(1, 200, r"\sqrt{2}"), _formula_image(1, 250, "z", decorative=True),
              ImageRegion(id="p", page=0, bbox=(0, 0, 10, 10), xref=2, alt="a photo", kind="image", latex=None)]
    found = mathml.collect(notation, images)
    assert [(f["name"], f["source"]) for f in found] == [
        ("page-001_formula-1.mml", "text"), ("page-002_formula-1.mml", "text"),
        ("page-002_formula-2.mml", "figure"), ("page-002_formula-3.mml", "text")]
    assert found[1]["mathml"] is None  # its LaTeX was unfinished: no MathML, the reading stays
    assert ET.fromstring(found[3]["mathml"]).get("display") == "block"  # a displayed Formula
    assert ET.fromstring(notation[1]["mathml"]).get("display") == "inline"  # kept with the reading for the report
    assert "mathml" not in notation[2]


def test_the_downloads_come_only_with_formulas(tmp_path, monkeypatch):
    monkeypatch.setenv("MATHCAT_BIN", str(tmp_path / "missing"))  # no MathCAT: no braille
    assert mathml.outputs(tmp_path, "doc", [], []) == ([], {})
    assert mathml.outputs(tmp_path, "doc", [{"page": 0, "bbox": [0, 0, 1, 1], "latex": r"\frac{a"}])[1] == {}
    assert not list(tmp_path.iterdir())
    notation = [{"page": 0, "bbox": [72, 100, 400, 120], "role": "Formula", "spoken": "a <b> over 2",
                 "latex": r"\frac{a}{2}"}]
    formulas, files = mathml.outputs(tmp_path, "doc", notation, [_formula_image(2, 80, r"\pi r^2")])
    assert files == {"math_files": "doc_math.zip", "math_list": "doc_formulas.html"}
    with zipfile.ZipFile(tmp_path / files["math_files"]) as z:
        assert sorted(z.namelist()) == ["README.txt", "formulas.html", "page-001_formula-1.mml",
                                        "page-003_formula-1.mml"]
        ET.fromstring(z.read("page-003_formula-1.mml"))  # a well-formed MathML file
    listing = (tmp_path / files["math_list"]).read_text()
    assert "<h2>Page 1</h2>" in listing and "<h2>Page 3</h2>" in listing and listing.count("<math ") == 2
    assert "a &lt;b&gt; over 2" in listing  # the spoken reading is text, escaped
    assert "default-src 'none'" in listing and "<script" not in listing


def fake_mathcat(tmp_path, speech: str) -> str:
    """A stand-in for mathcat-cli: every formula is read as `speech`, with a little braille."""
    exe = tmp_path / "mathcat-cli"
    exe.write_text(f"#!{sys.executable}\nimport json, sys\n"
                   "for line in sys.stdin:\n"
                   f"    print(json.dumps({{'speech': {speech!r}, 'nemeth': '\u282d\u2818\u2806', "
                   "'ueb': '\u282d\u2814\u283c\u2803'}), flush=True)\n")
    exe.chmod(0o755)
    return str(exe)


def test_a_run_with_formulas_reports_them_and_offers_the_files(samples, tmp_path, monkeypatch):
    """End to end with the mock AI and a stand-in MathCAT: the formulas come out in the report, as the math files and
    in braille; one whose two readings disagree is flagged with both, and listed for the engine's improvement."""
    from app.pipeline import orchestrator
    from app.pipeline.orchestrator import JobOptions, run_remediation
    from app.storage import JobStore

    real = mathml.collect

    def with_one(notation, images=()):
        # The mock reads no formula in the sample: give it one, as the AI would have
        notation.append({"page": 0, "bbox": [72, 100, 300, 120], "role": "Formula", "spoken": "E equals m c squared",
                         "latex": "E = mc^2", "kind": "math"})
        return real(notation, images)

    monkeypatch.setattr(orchestrator.mathml, "collect", with_one)
    monkeypatch.setenv("MATHCAT_BIN", fake_mathcat(tmp_path, "E is equal to m c cubed"))
    store = JobStore("00000000-0000-4000-8000-000000000403", root=tmp_path / "jobs")
    store.create()
    store.input_pdf.write_bytes((samples / "simple_report.pdf").read_bytes())
    result = asyncio.run(run_remediation(store, JobOptions(stem="doc", provider="mock")))
    reports = result["reports"]
    assert {k: reports[k] for k in ("math_files", "math_list", "math_nemeth", "math_ueb")} == {
        "math_files": "doc_math.zip", "math_list": "doc_formulas.html", "math_nemeth": "doc_math_nemeth.brf",
        "math_ueb": "doc_math_ueb.brf"}
    assert (store.output_dir / "doc_math_ueb.brf").read_bytes().startswith(b"FORMULAS UEB\r\n")
    formula = reports["math"][0]
    assert formula["page"] == 1 and formula["mathml"].startswith("<math ")
    assert formula["rule"] == {"spoken": "E is equal to m c cubed", "nemeth": "\u282d\u2818\u2806",
                               "ueb": "\u282d\u2814\u283c\u2803"}
    (d,) = reports["math_disagreements"]
    assert d["differs"]["numbers"] == {"ai_only": ["2"], "rule_only": ["3"]}
    assert (d["readings"], d["in_pdf"], d["chosen"]) == (
        {"ai": "E equals m c squared", "rule": "E is equal to m c cubed"}, "ai", None)
    flagged = [f for f in reports["tag_verification"]["primary"]["findings"]
               if f["issue"] == "two readings of a formula disagree"]
    assert len(flagged) == 1 and [r["source"] for r in flagged[0]["extra"]["readings"]] == ["ai", "rule"]
    assert len(flagged[0]["boxes"]) == 1  # the formula's block, for Show (#426)
    summary = reports["remediation_summary"]
    assert "Formulas: 1 saved as MathML" in summary and "1 disagree" in summary


def test_mathml_keeps_its_own_namespace_whatever_else_registered_one():
    """ElementTree's namespace registry is shared: another module setting a default must not prefix ours."""
    ET.register_namespace("", "http://example.com/other")
    try:
        assert mathml.to_mathml("x^2").startswith('<math xmlns="http://www.w3.org/1998/Math/MathML"')
    finally:
        ET._namespace_map.pop("http://example.com/other", None)


@pytest.mark.parametrize("latex,formula", [
    (r"y = h \quad (2.27)", "y = h"),
    (r"a = b \tag{3.1}", "a = b"),
    (r"|w| < \pi \qquad (4.2b).", r"|w| < \pi"),
    (r"x = y \quad \text{(model)} \quad (1.5)", r"x = y \quad \text{(model)}"),
    (r"f(2.5)", "f(2.5)"),  # part of the formula
    (r"y = 3 (2)", "y = 3 (2)"),
])
def test_an_equations_number_isnt_part_of_its_mathml(latex, formula):
    """Read as math, "(2.27)" after a formula is "times 2.27": the label goes, the LaTeX annotation keeps it."""
    assert mathml.without_label(latex) == formula
    m = mathml.to_mathml(latex)
    if formula != latex:
        assert "<mn>2.27</mn>" not in m and "<mn>3.1</mn>" not in m
    assert ET.fromstring(m).find(f".//{NS}annotation").text == latex
