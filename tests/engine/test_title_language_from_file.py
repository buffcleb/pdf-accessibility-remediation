"""The output's title and language come from the job, else from the file's own metadata, else from a heading or
the default. A file's metadata is its producer's to write: a title of thousands of characters, or a /Lang that is
not a language tag, was copied into the output as it was."""
from app.pipeline.models import DocumentAnalysis
from app.pipeline.orchestrator import JobOptions, _pick_title
from tests.engine.layout import block, document, page


def _analysis(title=None, language=None) -> DocumentAnalysis:
    return document(title=title, language=language)


def test_a_title_from_the_file_is_bounded():
    assert _pick_title(JobOptions(stem="doc"), _analysis(title="A short title")) == "A short title"
    assert len(_pick_title(JobOptions(stem="doc"), _analysis(title="x" * 50_000))) <= 1000
    assert _pick_title(JobOptions(stem="doc", title="Given"), _analysis(title="x" * 50_000)) == "Given"


def test_a_language_from_the_file_must_be_a_language_tag():
    from app.pipeline.orchestrator import _pick_language

    assert _pick_language(JobOptions(stem="doc"), _analysis(language="de-CH"), "en-US")[0] == "de-CH"
    assert _pick_language(JobOptions(stem="doc"), _analysis(language="not a language; drop tables"), "en-US")[0] == "en-US"
    assert _pick_language(JobOptions(stem="doc"), _analysis(language="x" * 300), "en-US")[0] == "en-US"
    assert _pick_language(JobOptions(stem="doc", language="fr"), _analysis(language="de-CH"), "en-US")[0] == "fr"
    assert _pick_language(JobOptions(stem="doc"), _analysis(), "en-US")[0] == "en-US"


def test_an_application_and_file_name_is_no_title():
    """Word and PowerPoint write "Microsoft Word - syllabus.docx" when the author gave no title; a reader announced
    it as the document's name. The first top-level heading is the title then."""
    from app.pipeline.orchestrator import usable_title

    for poor in ("Microsoft Word - syllabus_accessible.docx", "Microsoft PowerPoint - Lecture 3.pptx",
                 "Microsoft Word - Document1", "Microsoft Word", "microsoft excel - grades.xlsx", "syllabus.docx",
                 "Lab report final.PDF", "C:\\Users\\me\\Desktop\\notes.doc", "Untitled", "  ", None,
                 "PowerPoint Presentation"):
        assert not usable_title(poor), poor
    for good in ("Introduction to Physics", "Using Microsoft Word for Theses", "Word Problems in Algebra",
                 "Excel - The Basics", "Node.js in Practice", "Report v2.1"):
        assert usable_title(good), good

    a = document(page(block("b0", (72, 72, 300, 90), "Introduction to Physics", "H1", size=18)),
                 title="Microsoft Word - syllabus_accessible.docx")
    assert _pick_title(JobOptions(stem="syllabus"), a) == "Introduction to Physics"
    assert _pick_title(JobOptions(stem="syllabus_v2"), _analysis(title="Microsoft Word - x.docx")) == "syllabus v2"
