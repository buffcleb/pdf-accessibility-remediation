"""#340: a document's language comes from its text when the PDF gives none (or clearly the wrong one), and a passage
clearly in another language gets its own on its tag."""
import asyncio
import shutil
import uuid

import pikepdf
import pymupdf as fitz

from app.pipeline import language

SPANISH = ("La biblioteca de la universidad abre todos los días de la semana, desde las ocho de la mañana hasta "
           "las diez de la noche. Los estudiantes pueden reservar salas de estudio en grupo con una semana de "
           "anticipación, y el personal ayuda a encontrar libros, revistas y artículos en la colección digital. "
           "Durante el periodo de exámenes, el horario se amplía hasta la medianoche para que todos tengan un lugar "
           "tranquilo donde trabajar. ")
ENGLISH = ("The university library is open every day of the week, from eight in the morning until ten at night. "
           "Students can book group study rooms a week ahead, and the staff help find books, journals and articles "
           "in the digital collection. During the exam period the hours are extended until midnight so that "
           "everyone has a quiet place to work. ")
FRENCH = "La bibliothèque reste ouverte pendant toute la période des examens, et chacun peut y travailler en silence."


def test_the_document_language_follows_its_text():
    # Nothing declared: the text decides, when it's sure and long enough
    lang, note = language.document_language(None, SPANISH * 2, "en-US")
    assert lang == "es" and "none was declared" in note and "Spanish" in note
    assert language.document_language(None, ENGLISH * 2, "en-US") == ("en-US", None)  # as before: nothing to say
    assert language.document_language(None, "Tabla 3", "en-US") == ("en-US", None)  # too little to say
    # Declared but clearly wrong (a template's): replaced only with plenty of text
    lang, note = language.document_language("en-US", SPANISH * 6, "en-US")
    assert lang == "es" and "declared en-US" in note
    assert language.document_language("en-US", SPANISH, "en-US") == ("en-US", None)  # not enough to overrule it
    assert language.document_language("en-GB", ENGLISH * 6, "en-US") == ("en-GB", None)  # same language: kept
    lang, note = language.document_language("pl-PL", ENGLISH * 6, "en-US")  # a template's language over English
    assert lang == "en-US" and "declared pl-PL" in note


def test_a_passage_gets_its_language_only_when_clearly_another():
    assert language.passage_language(FRENCH, "en-US") == "fr"
    assert language.passage_language(FRENCH, "fr-CA") is None  # the document's own
    assert language.passage_language("Merci beaucoup", "en-US") is None  # too short to be sure
    assert language.passage_language("x = 3.14 + 2.71 * 9.81 / 6.67 - 1.60 + 8.31 = 11 22 33 44 55", "en-US") is None
    assert language.passage_language(ENGLISH, "en-US") is None


def _doc(path, lines, lang=None):
    from harness.make_samples import _fonts, _save

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    reg, _ = _fonts(page, embed=True)
    y = 72
    for text in lines:
        rect = fitz.Rect(72, y, 540, y + 140)
        page.insert_textbox(rect, text, fontsize=11, fontname=reg)
        y += 150
    _save(doc, path)
    if lang:
        with pikepdf.open(path, allow_overwriting_input=True) as pdf:
            pdf.Root.Lang = pikepdf.String(lang)
            pdf.save(path)
    return path


def _remediate(tmp_path, src):
    from app.pipeline.orchestrator import JobOptions, run_remediation
    from app.storage import JobStore

    jid = str(uuid.uuid4())
    store = JobStore(jid, root=tmp_path / "jobs")
    store.create()
    shutil.copyfile(src, store.input_pdf)
    res = asyncio.run(run_remediation(store, JobOptions(stem="l", job_id=jid, provider="mock")))
    return res, next(store.dir.rglob("l_remediated.pdf"))


def test_a_spanish_document_without_a_language_is_declared_spanish(tmp_path):
    res, out = _remediate(tmp_path, _doc(tmp_path / "es.pdf", [SPANISH, SPANISH, SPANISH]))
    with pikepdf.open(out) as pdf:
        assert str(pdf.Root.Lang) == "es"
    assert any("none was declared" in w for w in res["warnings"])


def test_a_french_sentence_in_an_english_document_is_tagged_french(tmp_path):
    res, out = _remediate(tmp_path, _doc(tmp_path / "en.pdf", [ENGLISH, FRENCH, ENGLISH], lang="en-US"))
    with pikepdf.open(out) as pdf:
        assert str(pdf.Root.Lang) == "en-US"
        langs = [str(o.Lang) for o in pdf.objects if isinstance(o, pikepdf.Dictionary) and o.get("/Type") == "/StructElem"
                 and "/Lang" in o]
    assert langs == ["fr"]
    assert any("1 passage(s) in another language" in w for w in res["warnings"])
