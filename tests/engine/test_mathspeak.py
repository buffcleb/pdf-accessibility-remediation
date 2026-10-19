"""#403: formulas read by rule (MathCAT) to check the AI's reading, braille files, and the person's choice between two
readings that disagree."""
import shutil

import pytest

from app.pipeline import mathspeak
from app.pipeline.edit_job import reading_choices
from tests.engine.test_mathml import fake_mathcat


@pytest.mark.parametrize("ai,rule", [
    ("x equals negative b plus or minus the square root of b squared minus 4 a c, all over 2 a",
     "x is equal to; the fraction with numerator; negative b plus or minus; the square root of b squared minus 4 eigh "
     "c; and denominator 2 eigh"),
    ("e to the i pi plus one equals zero", "e raised to the i pi power, plus 1; is equal to 0"),
    ("the integral from zero to one of x squared dx equals one third",
     "the integral from 0 to 1 of, x squared d x; is equal to 1 third"),
    ("the sum from n equals one to ten of n equals fifty-five",
     "the sum from n is equal to 1 to 10 of n; is equal to 55"),
    ("pi is about three point one four", "pi is approximately 3.14"),
    ("x to the second power", "x squared"),
    # Found on a real textbook chapter: the equation's number said after it, "with respect to", letters run together
    ("f of x equals x plus one; equation 4.12b", "f of x is equal to x plus 1"),
    ("g of s equals two s (3.7)", "g of s is equal to 2 s"),
    ("the integral of f of t with respect to t", "the integral of, f of t, d t"),
    ("ABC stable (DT)", "cap eigh  cap b  cap c, stable, open paren  cap d  cap t, close paren"),
    ("one over T1 times Vc", "the fraction with numerator 1, and denominator  cap t sub 1, cap v sub c"),
    ("it is x", "it is x"),
    ("where g of z equals 1", "whereG of z, is equal to 1"),  # a word the LaTeX ran into a letter
    ("y equals 2 x; (4.1b)", "y is equal to 2 x, open paren 4.1 b close paren"),  # the label on both sides
])
def test_the_same_formula_in_other_words_agrees(ai, rule):
    assert mathspeak.compare(ai, rule) is None


@pytest.mark.parametrize("ai,rule,differs", [
    ("x to the power of three", "x squared", {"numbers": {"ai_only": ["3"], "rule_only": ["2"]}}),
    ("y equals m x plus b", "y is equal to m x plus c", {"letters": {"ai_only": ["b"], "rule_only": ["c"]}}),
    ("alpha plus beta", "alpha plus gamma", {"letters": {"ai_only": ["beta"], "rule_only": ["gamma"]}}),
    ("2 x", "2 x plus 1", {"numbers": {"ai_only": [], "rule_only": ["1"]}}),
])
def test_another_number_or_letter_disagrees(ai, rule, differs):
    d = mathspeak.compare(ai, rule)
    for kind, want in differs.items():
        assert d[kind] == want


def test_mathcats_speech_reads_as_text():
    assert mathspeak.speech_text("x is equal to; 4 eigh c;") == "x is equal to, 4 a c"


def test_braille_files_hold_each_formula_under_its_heading():
    assert mathspeak.to_brf("⠭⠀⠨⠅⠀⠼⠂") == "X .K #1"  # x = 1 in Nemeth
    formulas = [{"page": 2, "name": "page-003_formula-2.mml", "rule": {"nemeth": "⠭" * 50}},
                {"page": 4, "name": "page-005_formula-1.mml", "rule": {"ueb": "⠭"}}]
    nemeth = mathspeak.brf(formulas, "nemeth", "d.pdf").split("\r\n")
    assert nemeth[:5] == ["FORMULAS NEMETH", "", "PAGE #C FORMULA #B", "X" * 40, "X" * 10]
    assert all(ord(c) < 128 for c in "".join(nemeth))
    assert mathspeak.brf([formulas[0]], "ueb", "d.pdf") is None
    many = [{"page": 0, "name": f"page-001_formula-{n}.mml", "rule": {"ueb": "⠭"}} for n in range(1, 20)]
    pages = mathspeak.brf(many, "ueb", "d.pdf").split("\f")
    assert len(pages) == 3 and all(len(p.rstrip("\r\n").split("\r\n")) <= mathspeak.PAGE_LINES for p in pages)


def test_without_mathcat_nothing_is_read(tmp_path, monkeypatch):
    monkeypatch.setenv("MATHCAT_BIN", str(tmp_path / "missing"))
    assert mathspeak.read_all(["<math/>"]) == []
    bad = tmp_path / "bad"
    bad.write_text("#!/bin/sh\necho not-json\n")
    bad.chmod(0o755)
    monkeypatch.setenv("MATHCAT_BIN", str(bad))
    assert mathspeak.read_all(["<math><mi>x</mi></math>", "<math><mi>y</mi></math>"]) == []  # a short answer: none


def test_formulas_read_by_rule_keep_braille_and_note_disagreements(tmp_path, monkeypatch):
    from app.pipeline import mathml
    monkeypatch.setenv("MATHCAT_BIN", fake_mathcat(tmp_path, "x cubed"))
    formulas = mathml.collect([{"page": 0, "bbox": [0, 0, 9, 9], "role": "Formula", "spoken": "x squared",
                                "latex": "x^2"},
                               {"page": 1, "bbox": [0, 0, 9, 9], "role": "Formula", "spoken": "x cubed",
                                "latex": "x^3"},
                               {"page": 2, "bbox": [0, 0, 9, 9], "role": "Formula", "spoken": "y", "latex": r"\frac{"}])
    assert mathml.read_by_rule(formulas) == 1
    assert formulas[0]["disagreement"]["numbers"] == {"ai_only": ["2"], "rule_only": ["3"]}
    assert "disagreement" not in formulas[1] and formulas[1]["rule"]["nemeth"]
    assert "rule" not in formulas[2]  # no MathML, nothing to read
    extra = mathml.finding_extra(formulas[0])
    assert [r["text"] for r in extra["readings"]] == ["x squared", "x cubed"] and extra["mathml"].startswith("<math")


@pytest.mark.skipif(shutil.which("mathcat-cli") is None, reason="MathCAT is built into the engine image")
def test_the_real_mathcat_reads_speech_and_both_braille_codes():
    from app.pipeline import mathml
    (answer,) = mathspeak.read_all([mathml.to_mathml(r"\frac{1}{2}")])
    assert mathspeak.speech_text(answer["speech"]) == "1 half"
    assert answer["nemeth"] and answer["ueb"] and answer["nemeth"] != answer["ueb"]


def test_a_persons_choice_between_readings_is_recorded():
    ops = [{"op": "set_alt", "element": 7, "expect_role": "Formula", "alt": "x cubed",
            "reading": {"formula": "page-001_formula-1.mml", "chose": "rule",
                        "readings": {"ai": "x squared", "rule": "x cubed"}}},
           {"op": "set_actual_text", "element": 9, "expect_role": "P", "text": "mine",
            "reading": {"formula": "f2", "chose": "nonsense", "readings": "not a dict"}},
           {"op": "set_alt", "element": 8, "expect_role": "Figure", "alt": "a chart"},
           {"op": "set_role", "element": 3, "expect_role": "P", "role": "H2", "reading": {"chose": "ai"}}]
    assert reading_choices(ops) == [
        {"formula": "page-001_formula-1.mml", "element": 7, "chose": "rule",
         "readings": {"ai": "x squared", "rule": "x cubed"}, "text": "x cubed"},
        {"formula": "f2", "element": 9, "chose": "own", "readings": {"ai": "", "rule": ""}, "text": "mine"}]
