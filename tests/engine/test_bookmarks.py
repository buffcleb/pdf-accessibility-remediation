"""Bookmarks made from the headings: nesting, where each one goes, and when a document's own outline is kept."""
import pikepdf
import pymupdf as fitz
import pytest
from pikepdf import Array, Dictionary, Name, String

from app.pipeline.orchestrator import prepare_analysis
from app.tagging.bookmarks import headings, nest, outline_state, write_bookmarks
from app.tagging.tagger import tag_document
from app.verification.integrity import check_integrity
from app.verification.tagmap import build_tagmap

BODY = "Some ordinary text in the chapter that is long enough to be a paragraph of body text."


@pytest.fixture
def chapters(tmp_path):
    """Three pages, each a chapter heading with one section under it, tagged by the engine."""
    doc = fitz.open()
    for n in range(3):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 90), f"Chapter {n + 1}: Growing tomatoes", fontsize=20, fontname="hebo")
        page.insert_text((72, 130), BODY, fontsize=11, fontname="helv")
        page.insert_text((72, 300), f"Section {n + 1}.1 Soil", fontsize=14, fontname="hebo")
        page.insert_text((72, 330), BODY, fontsize=11, fontname="helv")
    src = tmp_path / "chapters.pdf"
    doc.save(src)
    out = tmp_path / "chapters_tagged.pdf"
    tag_document(src, out, prepare_analysis(src), title="Tomatoes", language="en-US")
    return out


def _toc(path):
    with fitz.open(path) as doc:
        return [(lvl, title, page, dest.get("to")) for lvl, title, page, dest in doc.get_toc(simple=False)]


def _errors(path):
    with pikepdf.open(path) as pdf:
        findings, _ = check_integrity(pdf)
    return [f for f in findings if f.severity == "error"]


def test_headings_become_nested_bookmarks_that_go_to_each_heading(chapters):
    tm = build_tagmap(chapters)
    assert write_bookmarks(chapters, tm) == {"bookmarks": 6, "outline": "none"}
    toc = _toc(chapters)
    assert [(lvl, title[:9], page) for lvl, title, page, _ in toc] == [
        (1, "Chapter 1", 1), (2, "Section 1", 1), (1, "Chapter 2", 2), (2, "Section 2", 2),
        (1, "Chapter 3", 3), (2, "Section 3", 3)]
    # each goes to the top of its heading (shown from the top of the page), a little above it
    tops = {e["text"][:9]: e["boxes"][0]["bbox"][1] for e in tm["elements"] if e["role"] in ("H1", "H2")}
    for _, title, _, to in toc:
        assert tops[title[:9]] - 4 <= to.y <= tops[title[:9]]
    assert _errors(chapters) == []
    with pikepdf.open(chapters) as pdf:   # chapters open closed, showing their sections when opened
        first = pdf.Root.Outlines.First
        assert int(pdf.Root.Outlines.Count) == 3 and int(first.Count) == -1
        # and points at the heading's tag too (a structure destination, which PDF/UA-2 asks for)
        assert first.A.S == Name.GoTo and first.A.SD[0].S == Name.H1 and first.First.A.SD[0].S == Name.H2
        assert first.A.D[0].objgen == pdf.pages[0].obj.objgen


def test_bookmarks_made_here_are_rebuilt_and_a_documents_own_are_kept(chapters, tmp_path):
    tm = build_tagmap(chapters)
    write_bookmarks(chapters, tm)
    assert write_bookmarks(chapters, tm) == {"bookmarks": 6, "outline": "ours"}   # brought up to date, not doubled
    assert len(_toc(chapters)) == 6
    # The author's own outline, which works, stays as it is
    with pikepdf.open(chapters, allow_overwriting_input=True) as pdf:
        own = pdf.make_indirect(Dictionary(Title=String("The author's way in"), Dest=Array([pdf.pages[2].obj, Name.Fit])))
        pdf.Root.Outlines = pdf.make_indirect(Dictionary(Type=Name.Outlines, First=own, Last=own, Count=1))
        own.Parent = pdf.Root.Outlines
        pdf.save(chapters)
    assert write_bookmarks(chapters, tm) == {"bookmarks": 0, "outline": "kept"}
    assert [t for _, t, _, _ in _toc(chapters)] == ["The author's way in"]


def test_an_outline_that_goes_nowhere_is_replaced(chapters):
    with pikepdf.open(chapters, allow_overwriting_input=True) as pdf:
        stray = pdf.make_indirect(Dictionary(Type=Name.Page))  # not one of the document's pages
        dead = pdf.make_indirect(Dictionary(Title=String("Old"), Dest=Array([stray, Name.Fit])))
        empty = pdf.make_indirect(Dictionary(Title=String("Older"), Prev=dead))
        dead.Next = empty
        pdf.Root.Outlines = pdf.make_indirect(Dictionary(Type=Name.Outlines, First=dead, Last=empty, Count=2))
        assert outline_state(pdf) == "broken"
        pdf.save(chapters)
    assert write_bookmarks(chapters, build_tagmap(chapters)) == {"bookmarks": 6, "outline": "broken"}


def test_after_edits_only_bookmarks_made_here_follow_the_headings(chapters):
    tm = build_tagmap(chapters)
    assert write_bookmarks(chapters, tm, rebuild_only=True) == {"bookmarks": 0, "outline": "none"}
    assert _toc(chapters) == []
    write_bookmarks(chapters, tm)
    # Every heading but one made a paragraph: one heading is no outline, so the one made here goes
    few = {**tm, "elements": [e if e["role"] != "H2" and not (e["role"] == "H1" and "Chapter 1" not in e.get("text", ""))
                              else {**e, "role": "P"} for e in tm["elements"]]}
    assert write_bookmarks(chapters, few, rebuild_only=True) == {"bookmarks": 0, "outline": "ours"}
    assert _toc(chapters) == []


def _el(i, role, text, page=1, top=100.0, **kw):
    return {"i": i, "role": role, "text": text, "boxes": [{"page": page, "bbox": [72, top, 300, top + 14]}], **kw}


def test_levels_titles_and_what_is_left_out():
    tm = {"elements": [
        _el(0, "H1", "Introduction"),
        _el(1, "H", "Unnumbered heading after an H1"),       # takes the level before it
        _el(2, "H3", "A skipped level nests one deeper"),
        _el(3, "H2", "   spaced\n out   words  "),
        _el(4, "H2", "Ignored words", actual_text="What a screen reader says"),
        _el(5, "H2", "", page=2),                            # nothing to call it
        {"i": 6, "role": "H2", "text": "No place on any page", "boxes": []},
        _el(7, "H2", "word " * 60),
        _el(8, "P", "Not a heading"),
    ]}
    flat = headings(tm)
    assert [(e.level, e.title[:25]) for e in flat] == [
        (1, "Introduction"), (1, "Unnumbered heading after "), (3, "A skipped level nests one"),
        (2, "spaced out words"), (2, "What a screen reader says"), (2, "word word word word word ")]
    assert len(flat[-1].title) == 120 and flat[-1].title.endswith("…")
    top = nest(flat)
    assert [e.title[:5] for e in top] == ["Intro", "Unnum"]
    assert [e.title[:5] for e in top[1].kids] == ["A ski", "space", "What ", "word "]


def test_a_turned_page_gets_a_bookmark_that_opens_the_page(chapters):
    with pikepdf.open(chapters, allow_overwriting_input=True) as pdf:
        pdf.pages[1].obj.Rotate = 90
        pdf.save(chapters)
    write_bookmarks(chapters, build_tagmap(chapters))
    with pikepdf.open(chapters) as pdf:
        second = pdf.Root.Outlines.First.Next
        assert str(second.Title).startswith("Chapter 2")
        assert list(second.A.D)[1:] == [Name.XYZ, None, None, None]
