"""#403: formulas read a second time, from their pictures, by the formula reading service (docker/formula), and
compared with the AI's reading: where they disagree, a person chooses."""
import base64
import importlib.util
import json
import sys
import threading
from pathlib import Path

import fakeredis
import pymupdf as fitz
import pytest

from app.config import get_settings
from app.pipeline import formula_ocr, mathml
from app.pipeline.edit_job import reading_choices
from tests.engine.test_mathml import fake_mathcat


@pytest.fixture
def queue(monkeypatch):
    monkeypatch.setattr(get_settings(), "formula_ocr_timeout_s", 2, raising=False)
    return fakeredis.FakeStrictRedis()


def _serve(client, answers):
    """Stand in for the service: answer each request as it comes."""
    def run():
        for answer in answers:
            got = client.brpop([formula_ocr.QUEUE], timeout=5)
            if not got:
                return
            req = json.loads(got[1])
            assert base64.b64decode(req["png"]).startswith(b"\x89PNG")
            client.lpush(formula_ocr.RESULT.format(id=req["id"]), json.dumps(answer))
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def test_readings_come_back_in_the_order_asked(queue):
    t = _serve(queue, [{"ok": True, "latex": "x^2"}, {"ok": False, "error": "nothing read"}])
    got = formula_ocr.read_all([b"\x89PNG one", b"\x89PNG two"], client=queue)
    t.join(5)
    assert got == [{"ok": True, "latex": "x^2"}, {"ok": False, "error": "nothing read"}]


def test_a_service_that_never_answers_costs_one_wait_and_leaves_nothing_queued(queue):
    got = formula_ocr.read_all([b"\x89PNG one", b"\x89PNG two"], client=queue)
    assert [g["ok"] for g in got] == [False, False]
    assert queue.llen(formula_ocr.QUEUE) == 0


def test_each_formula_is_pictured_from_its_place_on_the_page(tmp_path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((100, 200), "x = 2y + 1", fontsize=14)
    path = tmp_path / "f.pdf"
    doc.save(path)
    pngs = formula_ocr.pictures(path, [{"page": 0, "bbox": [95, 185, 190, 205]}, {"page": 3, "bbox": [0, 0, 9, 9]},
                                       {"page": 0, "bbox": None}])
    assert pngs[0].startswith(b"\x89PNG") and pngs[1] is None and pngs[2] is None
    assert fitz.Pixmap(pngs[0]).width > 250  # 101 points wide with its margin, at 200 dpi


def test_a_picture_that_says_other_numbers_is_flagged_with_every_reading(tmp_path, monkeypatch):
    monkeypatch.setenv("MATHCAT_BIN", fake_mathcat(tmp_path, "x squared"))
    formulas = mathml.collect([{"page": 0, "bbox": [10, 10, 90, 30], "role": "Formula", "spoken": "x squared",
                                "latex": "x^2"}])
    assert mathml.read_by_rule(formulas) == 0  # the AI's reading and its LaTeX agree
    monkeypatch.setattr(formula_ocr, "pictures", lambda pdf, fs: [b"\x89PNG"] * len(fs))
    monkeypatch.setattr(formula_ocr, "read_all", lambda images: [{"ok": True, "latex": "x^3"}])
    # MathCAT reads the picture's MathML as "x cubed"
    monkeypatch.setenv("MATHCAT_BIN", _two_readings(tmp_path, {"x^2": "x squared", "x^3": "x cubed"}))
    assert mathml.read_from_pictures(formulas, tmp_path / "unused.pdf") == 1
    f = formulas[0]
    assert f["picture"]["latex"] == "x^3" and f["picture"]["spoken"] == "x cubed"
    assert f["picture_disagreement"]["numbers"] == {"ai_only": ["2"], "rule_only": ["3"]}
    assert mathml.flagged(f)
    (d,) = mathml.disagreements(formulas)
    assert d["readings"] == {"ai": "x squared", "rule": "x squared", "picture": "x cubed"}
    assert d["differs"] is None and d["picture_latex"] == "x^3"
    extra = mathml.finding_extra(f)
    assert [r["source"] for r in extra["readings"]] == ["ai", "rule", "picture"]
    assert extra["picture_mathml"].startswith("<math")


def _two_readings(tmp_path, by_latex: dict) -> str:
    """A stand-in MathCAT that reads each MathML by the LaTeX annotation in it."""
    exe = tmp_path / "mathcat-two"
    exe.write_text(f"#!{sys.executable}\nimport json, sys\nreadings = {by_latex!r}\n"
                   "for line in sys.stdin:\n"
                   "    m = json.loads(line)['mathml']\n"
                   "    said = next((v for k, v in readings.items() if '>' + k + '<' in m), 'unknown')\n"
                   "    print(json.dumps({'speech': said, 'nemeth': '\\u282d', 'ueb': '\\u282d'}), flush=True)\n")
    exe.chmod(0o755)
    return str(exe)


def test_without_the_service_answering_formulas_are_left_as_they_were(tmp_path, monkeypatch):
    monkeypatch.setenv("MATHCAT_BIN", fake_mathcat(tmp_path, "x squared"))
    formulas = mathml.collect([{"page": 0, "bbox": [10, 10, 90, 30], "role": "Formula", "spoken": "x squared",
                                "latex": "x^2"}])
    mathml.read_by_rule(formulas)
    monkeypatch.setattr(formula_ocr, "pictures", lambda pdf, fs: [b"\x89PNG"] * len(fs))
    monkeypatch.setattr(formula_ocr, "read_all", lambda images: [{"ok": False, "error": "no answer"}])
    assert mathml.read_from_pictures(formulas, tmp_path / "unused.pdf") == 0
    assert "picture" not in formulas[0] and not mathml.flagged(formulas[0])


def test_a_choice_of_the_pictures_reading_is_recorded():
    ops = [{"op": "set_alt", "element": 4, "expect_role": "Formula", "alt": "x cubed",
            "reading": {"formula": "f1", "chose": "picture",
                        "readings": {"ai": "x squared", "rule": "x squared", "picture": "x cubed"}}}]
    assert reading_choices(ops)[0]["chose"] == "picture"
    assert reading_choices(ops)[0]["readings"]["picture"] == "x cubed"


# --------------------------------------------------------------------------- the service itself
def _service():
    path = Path(__file__).resolve().parents[2] / "docker/formula/serve.py"
    spec = importlib.util.spec_from_file_location("formula_serve", path)
    serve = importlib.util.module_from_spec(spec)
    sys.modules["formula_serve"] = serve
    spec.loader.exec_module(serve)
    return serve


def test_the_service_answers_each_request_and_refuses_what_isnt_a_picture(monkeypatch):
    serve = _service()
    monkeypatch.setattr(serve, "model", lambda: (lambda png: ("x^{2}", 0.1)))
    client = fakeredis.FakeStrictRedis()
    png = b"\x89PNG\r\n\x1a\nrest"
    serve.handle(client, json.dumps({"id": "a", "png": base64.b64encode(png).decode()}).encode())
    serve.handle(client, json.dumps({"id": "b", "png": base64.b64encode(b"GIF89a").decode()}).encode())
    serve.handle(client, json.dumps({"id": "c", "png": "", "expires": 1}).encode())  # nobody waiting any more
    serve.handle(client, b"not json")
    assert json.loads(client.lpop("formula:result:a")) == {"ok": True, "latex": "x^{2}"}
    assert json.loads(client.lpop("formula:result:b"))["ok"] is False
    assert client.lpop("formula:result:c") is None
    assert client.ttl("formula:result:a") == -2 or client.ttl("formula:result:a") > 0
    assert serve.stats["read"] == 1 and serve.stats["not_read"] == 1


@pytest.mark.parametrize("raw,clean", [
    (r"h[n]e^{-j\Omega n}\qquad\qquad\qquad\left(2.17\right)", r"h[n]e^{-j\Omega n}"),
    (r"|w|<\pi\ .\qquad\qquad(2.62\mathrm{b})", r"|w|<\pi"),
    (r"y=\frac{1}{2}\mathrm{~\\\\\\}\ \ ", r"y=\frac{1}{2}"),
    (r"x^{2}+1", r"x^{2}+1"),
    (r"f(2.5)", r"f(2.5)"),  # part of the formula
    (r"\begin{array}{c}{{\mathrm{where}}}\end{array}\qquad H(z)", None),  # took in the text around it
    (r"x\qquad\qquad\qquad\qquad\qquad\qquad\qquad y", None),  # padding it couldn't read past
    (r"\nonumber\rightarrow\pm", None),
    ("", None),
])
def test_a_picture_readers_latex_is_made_comparable_or_set_aside(raw, clean):
    assert mathml.picture_latex(raw) == clean


def test_readings_of_very_different_length_arent_compared():
    assert mathml._comparable("x is equal to 1 half", "x is equal to 1 half")
    assert not mathml._comparable("x of n arrow cap h of e raised to the j omega power arrow y of n", "x of n")
