"""#631: a table of contents entry with no link of its own is pointed at the one heading with its words, and flagged
for a person to check; one no heading matches is flagged as going nowhere; and a person sets either in the tag
manager (set_toc_target), which ends the flag."""
import asyncio

import pikepdf
from pikepdf import Array, Dictionary, Name, String

from app.tagging.editor import EditError, apply_edits
from app.tagging.pdfua2 import upgrade
from app.verification.integrity import check_integrity
from app.verification.verify import VerifyOptions, carried_ai_read, verify_output
from tests.engine.test_pdfua2 import _elems, _tagged


def _with_toc(tmp_path):
    """The book with a table of contents first: "Notes ..... 3", whose words one heading has, and "Chapter One
    ..... 1", which two headings have (the chapter's and its label among the notes). Neither has a link."""
    src = _tagged(tmp_path)
    with pikepdf.open(src, allow_overwriting_input=True) as pdf:
        doc = pdf.Root.StructTreeRoot.K[0] if isinstance(pdf.Root.StructTreeRoot.K, Array) \
            else pdf.Root.StructTreeRoot.K
        toc = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.TOC, P=doc, K=Array()))
        for said in ("Notes ........ 3", "Chapter One ........ 1"):
            toc.K.append(pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.TOCI, P=toc, K=Array(),
                                                      ActualText=String(said))))
        doc.K = Array([toc, *list(doc.K)])
        pdf.save(src)
    return src


def _upgraded(tmp_path):
    out, guessed = tmp_path / "ua2.pdf", []
    changes = upgrade(_with_toc(tmp_path), out, guessed)
    return out, guessed, changes


def _index(path):
    with pikepdf.open(path) as pdf:
        _, index = check_integrity(pdf)
        return [(e.index, e.role) for e in index.elements]


def test_an_entry_without_a_link_goes_to_the_one_heading_with_its_words(tmp_path):
    out, guessed, changes = _upgraded(tmp_path)
    assert any("pointed at the heading of the same text" in c for c in changes)
    with pikepdf.open(out) as pdf:
        notes, chapter = [e for e in _elems(pdf) if e.get("/S") == Name.TOCI]
        assert str(notes.Ref[0].S).startswith("/H") and "/Ref" not in chapter   # two headings say "Chapter One"
    (place, heading), = guessed
    assert heading == "Notes" and dict(_index(out))[place] == "TOCI"          # numbered as the tag map numbers it


def test_the_guess_is_flagged_and_an_entry_going_nowhere_too(tmp_path):
    out, guessed, _ = _upgraded(tmp_path)
    rep = asyncio.run(verify_output(out, VerifyOptions(ai=False, tagmap=False, toc_guesses=guessed)))
    by_issue = {f.issue: f for f in rep.findings if f.role == "TOCI"}
    guess, nowhere = by_issue["contents entry pointed at a heading by its text"], by_issue["contents entry goes nowhere"]
    assert guess.severity == nowhere.severity == "review"
    assert guess.element == guessed[0][0] and "“Notes”" in guess.detail
    assert guess.extra["ai_read"][2:] == ["toc-target", "Notes"]
    # A reviewed version flags the guess again from what the portal sends back, unless a person set that entry
    carried = [{"ai_read": guess.extra["ai_read"], "element": guess.element}]
    assert len(carried_ai_read(carried, [])) == 1
    assert carried_ai_read(carried, [{"op": "set_toc_target", "element": guess.element, "target": 3}]) == []
    assert len(carried_ai_read(carried, [{"op": "set_alt", "element": guess.element, "alt": "x"}])) == 1


def test_a_person_sets_or_clears_an_entrys_target(tmp_path):
    out, guessed, _ = _upgraded(tmp_path)
    roles = _index(out)
    chapter = [i for i, r in roles if r == "TOCI"][1]
    heading = next(i for i, r in roles if r.startswith("H"))
    done = tmp_path / "edited.pdf"
    log = apply_edits(out, done, [{"op": "set_toc_target", "element": chapter, "expect_role": "TOCI",
                                   "target": heading}])
    assert log == [f"element {chapter}: goes to element {heading}"]
    with pikepdf.open(done) as pdf:
        entry = [e for e in _elems(pdf) if e.get("/S") == Name.TOCI][1]
        assert str(entry.Ref[0].S).startswith("/H")
    rep = asyncio.run(verify_output(done, VerifyOptions(ai=False, tagmap=False)))
    assert not [f for f in rep.findings if f.issue == "contents entry goes nowhere"]
    cleared = tmp_path / "cleared.pdf"
    apply_edits(done, cleared, [{"op": "set_toc_target", "element": chapter, "expect_role": "TOCI", "target": None}])
    with pikepdf.open(cleared) as pdf:
        assert "/Ref" not in [e for e in _elems(pdf) if e.get("/S") == Name.TOCI][1]
    # Only an entry has a target, and it isn't another part of a contents list
    for op, why in (({"element": heading, "expect_role": None, "target": chapter}, "only a table of contents entry"),
                    ({"element": chapter, "expect_role": "TOCI", "target": chapter - 1}, "part of a table of contents")):
        try:
            apply_edits(out, tmp_path / "bad.pdf", [{"op": "set_toc_target", **op}])
            raise AssertionError("refused")
        except EditError as exc:
            assert why in str(exc)
