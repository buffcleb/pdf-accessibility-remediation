"""The output's title and language come from the job, else from the file's own metadata, else from a heading or
the default. A file's metadata is its producer's to write: a title of thousands of characters, or a /Lang that is
not a language tag, was copied into the output as it was."""
from app.pipeline.models import DocumentAnalysis
from app.pipeline.orchestrator import JobOptions, _pick_title


def _analysis(title=None, language=None) -> DocumentAnalysis:
    return DocumentAnalysis(page_count=0, pages=[], title=title, language=language, has_struct_tree=False,
                            is_marked=False)


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
