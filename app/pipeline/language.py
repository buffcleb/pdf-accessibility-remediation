"""A document's language, and passages in another one (#340).

A PDF that doesn't say its language was declared English (the default), and one whose template says English over
Spanish text kept it: a screen reader then reads the whole document with the wrong voice (WCAG 3.1.1), and veraPDF
can't tell. Here the text is asked instead:

- No language given (by the job or the file): the body text's language when the detector is sure over enough text,
  else the default.
- A language given that the text clearly isn't in: replaced only when the detector is very sure over a good amount
  of text; the report says so.
- A passage in another language (a quoted sentence): its tag gets that language when the detector is very sure, the
  passage is a sentence or more and mostly letters, and the language is a common one. The detector's confusions on
  short or technical text (names, formulas, Latin terms) stay out; the tag review still flags what's left.
"""
from __future__ import annotations

from functools import lru_cache

# Sure enough to set a missing language / to replace a declared one / to tag a passage
SURE, VERY_SURE, PASSAGE_SURE = 0.95, 0.99, 0.99
# How much text: letters for the document, letters to replace a declared language, words for a passage
DOC_LETTERS, OVERRIDE_LETTERS, PASSAGE_WORDS = 300, 1500, 12
# Languages a passage may be tagged with automatically (the detector's other 70-odd show up as confusions on short text)
COMMON = {"en", "es", "fr", "de", "it", "pt", "nl", "sv", "da", "nb", "no", "fi", "pl", "cs", "ru", "uk", "el", "tr",
          "ar", "he", "hi", "zh", "ja", "ko", "vi", "id"}
NAMES = {"en": "English", "es": "Spanish", "fr": "French", "de": "German", "it": "Italian", "pt": "Portuguese",
         "nl": "Dutch", "sv": "Swedish", "da": "Danish", "nb": "Norwegian", "no": "Norwegian", "fi": "Finnish",
         "pl": "Polish", "cs": "Czech", "ru": "Russian", "uk": "Ukrainian", "el": "Greek", "tr": "Turkish",
         "ar": "Arabic", "he": "Hebrew", "hi": "Hindi", "zh": "Chinese", "ja": "Japanese", "ko": "Korean",
         "vi": "Vietnamese", "id": "Indonesian"}


@lru_cache(maxsize=1)
def _identifier():
    try:
        from py3langid.langid import MODEL_FILE, LanguageIdentifier
    except ImportError:
        return None
    return LanguageIdentifier.from_model_file(MODEL_FILE, norm_probs=True)


def detect(text: str) -> tuple[str | None, float]:
    ident = _identifier()
    if ident is None or not text.strip():
        return None, 0.0
    lang, prob = ident.classify(text)
    return lang, float(prob)


def _letters(text: str) -> int:
    return sum(ch.isalpha() for ch in text)


def primary(tag: str | None) -> str:
    return (tag or "").split("-")[0].strip().lower()


def document_language(declared: str | None, text: str, default: str) -> tuple[str, str | None]:
    """The language to declare, and a note for the report when the text decided it. `declared`: the job's or the
    file's own, if well-formed."""
    sample = text[:60000]
    lang, prob = detect(sample) if _letters(sample) >= DOC_LETTERS else (None, 0.0)
    name = NAMES.get(lang or "", lang)
    if not declared:
        if lang and prob >= SURE and primary(default) != lang:
            return lang, f"Language: none was declared; the text is {name}, so it's declared {lang}."
        return default, None  # the text is in the default's language, or too little to say: as before
    if lang and prob >= VERY_SURE and _letters(sample) >= OVERRIDE_LETTERS and lang != primary(declared):
        chosen = default if primary(default) == lang else lang  # "en-US" rather than "en", when that's the default
        return chosen, f"Language: the file declared {declared}, but the text is {name}, so it's declared {chosen}."
    return declared, None


def passage_language(text: str, doc_lang: str) -> str | None:
    """The language to tag a passage with, when it clearly isn't the document's."""
    if len(text.split()) < PASSAGE_WORDS or _letters(text) < 0.7 * len(text.replace(" ", "")):
        return None
    lang, prob = detect(text)
    if lang and prob >= PASSAGE_SURE and lang in COMMON and lang != primary(doc_lang):
        return lang
    return None
