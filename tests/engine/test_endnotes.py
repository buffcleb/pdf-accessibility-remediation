"""Endnotes (#285): a "Notes" section whose entries are numbered, by chapter, becomes Notes with IDs; the raised marks
in the text, pages away, name them (/Ref) and are said "endnote 1". A note carried onto the next page stays one Note.
Two footnotes set one after another, each with its raised number, are two notes."""
import pikepdf
import pymupdf as fitz

from app.pipeline.orchestrator import prepare_analysis
from app.tagging.tagger import tag_document
from app.verification.integrity import check_integrity
from tests.engine.test_footnotes import _as_a_producer_tags_it

BODY = ["The survey covered forty sites across the region in spring and",
        "the results were consistent with earlier work on the soils there",
        "Nothing else of note happened during the second visit to the coast",
        "and the team returned home at the end of the month as planned."]


def _marked(page, y, line, mark):
    """A line of body text with a raised mark after its last word."""
    page.insert_text((72, y), line, fontsize=11, fontname="helv")
    page.insert_text((72 + fitz.get_text_length(line, "helv", 11), y - 4), mark, fontsize=7, fontname="helv")


def _book(tmp_path, chapter_two_marks=("1",), note_lines=None):
    """Two chapters with raised marks (1 and 2, then 1 again), and a Notes section at the end: a label per chapter,
    numbered notes, the second note of chapter one running onto the next page, and a Bibliography after it."""
    doc = fitz.open()
    for title, marks in (("Chapter One", ("1", "2")), ("Chapter Two", chapter_two_marks)):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 90), title, fontsize=20, fontname="hebo")
        y = 130
        for n, line in enumerate(BODY * 2):
            if n < len(marks):
                _marked(page, y, line, marks[n])
            else:
                page.insert_text((72, y), line, fontsize=11, fontname="helv")
            y += 16
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 40), "Notes to Pages 1–2", fontsize=9, fontname="helv")
    page.insert_text((72, 90), "Notes", fontsize=20, fontname="hebo")
    page.insert_text((72, 130), "Chapter One", fontsize=10, fontname="helv")
    y = 150
    for line in note_lines or ["1. Smith and Jones, Soils of the North, second edition, 2019.",
                               "2. Brown, A Survey of the Coast, which was the first to describe"]:
        page.insert_text((72, y), line, fontsize=10, fontname="helv")
        y += 13
    for n in range(40):  # the rest of the page: more of note 2, down to the foot
        page.insert_text((72, y), "the sites and their soils in the detail the later surveys used", fontsize=10,
                         fontname="helv")
        y += 13
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 40), "Notes to Pages 2–3", fontsize=9, fontname="helv")
    page.insert_text((72, 80), "every year after it, all the way to the present day.", fontsize=10, fontname="helv")
    page.insert_text((72, 110), "Chapter Two", fontsize=10, fontname="helv")
    page.insert_text((72, 130), "1. Green, Field Notes from the Return Journey, 2020.", fontsize=10, fontname="helv")
    page.insert_text((72, 180), "Bibliography", fontsize=20, fontname="hebo")
    page.insert_text((72, 210), "Smith, J. and Jones, K. Soils of the North. Second edition. 2019.", fontsize=11,
                     fontname="helv")
    src = tmp_path / "book.pdf"
    doc.save(src)
    return src


def _blocks(a):
    return [b for p in a.pages for b in p.blocks]


def test_numbered_notes_under_a_notes_heading_are_endnotes_by_chapter(tmp_path):
    a = prepare_analysis(_book(tmp_path))
    blocks = _blocks(a)
    notes = [b for b in blocks if b.role == "Note" and not b.continues]
    assert [(b.page, b.note_mark, b.note_kind) for b in notes] == [(2, "1", "endnote"), (2, "2", "endnote"),
                                                                   (3, "1", "endnote")]
    # The chapter labels are headings under the section's; the running headers are page furniture
    assert [(b.text, b.role) for b in blocks if b.text in ("Chapter One", "Chapter Two") and b.page >= 2] == \
        [("Chapter One", "H2"), ("Chapter Two", "H2")]
    assert all(b.role == "Artifact" for b in blocks if b.text.startswith("Notes to Pages"))
    # Note 2's last line, at the top of the next page, continues it
    cont = next(b for b in blocks if b.text.startswith("every year after it"))
    assert cont.role == "Note" and cont.continues == notes[1].id
    # The Bibliography isn't part of the section
    assert next(b for b in blocks if b.text.startswith("Smith, J.")).role == "P"
    # Each chapter's marks point at its own notes, and are said as endnotes
    marked = [b for b in blocks if b.marks]
    assert [(b.page, b.mark_notes) for b in marked] == [(0, [notes[0].id, notes[1].id]), (1, [notes[2].id])]
    assert "(endnote 1)" in marked[0].actual_text and "(endnote 2)" in marked[0].actual_text


def test_endnotes_are_tagged_as_notes_the_marks_name_across_pages(tmp_path):
    src = _book(tmp_path)
    out = tmp_path / "book_tagged.pdf"
    result = tag_document(src, out, prepare_analysis(src), title="Book", language="en-US")
    assert result.footnotes == 3 and result.footnote_references == 3
    with pikepdf.open(out) as pdf:
        findings, index = check_integrity(pdf)
        assert [f for f in findings if f.severity == "error"] == []
        notes = [e for e in index.elements if e.role == "Note"]
        assert len(notes) == 3 and all(bytes(n.obj.ID).startswith(b"note-") for n in notes)
        refs = [e for e in index.elements if e.role == "Reference"]
        assert [str(r.obj.ActualText) for r in refs] == ["endnote 1", "endnote 2", "endnote 1"]
        assert [r.obj.Ref[0].objgen for r in refs] == [n.obj.objgen for n in notes]
        pages = {p.objgen: i for i, p in enumerate(pdf.pages)}
        assert [pages[r.obj.Pg.objgen] for r in refs] == [0, 0, 1] and pages[notes[0].obj.Pg.objgen] == 2
        # The continuation's content is in note 2's own tag, on the page it is drawn on
        on = {pages[k.Pg.objgen] for k in notes[1].obj.K if isinstance(k, pikepdf.Dictionary) and "/Pg" in k}
        assert 3 in on


def test_marks_that_dont_fall_into_one_run_per_chapter_are_left_alone(tmp_path):
    # Three runs of marks (1 2, 1, 1) for two chapters of notes: which is which can't be told, and three numbered
    # lines nothing points at are as likely a list
    src = _book(tmp_path, chapter_two_marks=("1", "2", "1"))
    a = prepare_analysis(src)
    assert all(n is None for b in _blocks(a) if b.marks for n in b.mark_notes)
    assert [b for b in _blocks(a) if b.role == "Note"] == []


def _notes_page(tmp_path, head, count):
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    for n in range(12):
        page.insert_text((72, 80 + 16 * n), BODY[n % 4], fontsize=11, fontname="helv")
    page.insert_text((72, 300), head, fontsize=14, fontname="hebo")
    for n in range(count):
        page.insert_text((72, 330 + 14 * n), f"{n + 1}. Store the sample tray away from bright light, item {n + 1}.",
                         fontsize=11, fontname="helv")
    src = tmp_path / "notes_page.pdf"
    doc.save(src)
    return src


def test_a_long_notes_section_is_taken_without_marks_and_a_short_one_or_notes_colon_is_not(tmp_path):
    a = prepare_analysis(_notes_page(tmp_path, "Notes", 12))
    assert [b.note_mark for b in _blocks(a) if b.role == "Note"] == [str(n) for n in range(1, 13)]
    for head, count in (("Notes", 3), ("Notes:", 12)):  # a few tips under "Notes"; "Notes:" leads into a list
        a = prepare_analysis(_notes_page(tmp_path, head, count))
        assert [b for b in _blocks(a) if b.role == "Note"] == [], head


def test_a_notes_heading_over_prose_is_not_an_endnotes_section(tmp_path):
    src = _book(tmp_path, note_lines=["Some remarks on the method, which the survey followed",
                                      "throughout, before the notes proper begin later on."])
    a = prepare_analysis(src)
    assert [b for b in _blocks(a) if b.role == "Note"] == []
    assert all(n is None for b in _blocks(a) if b.marks for n in b.mark_notes)


def _two_footnotes(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    for n, line in enumerate(BODY * 3):
        if n in (1, 3):
            _marked(page, 100 + 16 * n, line, "1" if n == 1 else "2")
        else:
            page.insert_text((72, 100 + 16 * n), line, fontsize=11, fontname="helv")
    for n, text in enumerate(["Smith and Jones, Soils of the North, second edition.",
                              "Brown, A Survey of the Coast, the first to describe it."]):
        y = 700 + 11 * n
        page.insert_text((72, y - 3), str(n + 1), fontsize=5, fontname="helv")
        page.insert_text((76, y), text, fontsize=8, fontname="helv")
    src = tmp_path / "two.pdf"
    doc.save(src)
    return src


def test_two_footnotes_one_under_the_other_are_two_notes(tmp_path):
    a = prepare_analysis(_two_footnotes(tmp_path))
    notes = [b for b in _blocks(a) if b.role == "Note"]
    assert [(b.note_mark, b.note_kind) for b in notes] == [("1", "footnote"), ("2", "footnote")]
    said = next(b for b in _blocks(a) if b.marks).actual_text
    assert "(footnote 1)" in said and "(footnote 2)" in said


def test_two_footnotes_a_producer_put_in_one_note_stay_in_it(tmp_path):
    """Word tags two footnotes set one under the other as one Footnote holding a paragraph each. Each paragraph
    isn't made a Note inside it: the marks name the producer's Note."""
    first = tmp_path / "first.pdf"
    src = _two_footnotes(tmp_path)
    tag_document(src, first, prepare_analysis(src), title="Notes", language="en-US")
    producer = _as_a_producer_tags_it(first, tmp_path / "producer.pdf")
    with pikepdf.open(producer, allow_overwriting_input=True) as pdf:
        index = check_integrity(pdf)[1]
        notes = [e for e in index.elements if e.role == "P"][-2:]   # the two footnote paragraphs, last on the page
        parent = notes[0].obj.P
        note = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.StructElem, S=pikepdf.Name.Note, P=parent,
                                                    K=pikepdf.Array([n.obj for n in notes]), Pg=pdf.pages[0].obj))
        kids = [k for k in parent.K if not (isinstance(k, pikepdf.Dictionary) and k.objgen in
                                            {n.obj.objgen for n in notes})]
        parent.K = pikepdf.Array(kids + [note])
        for n in notes:
            n.obj.P = note
        pdf.save(producer)
    out = tmp_path / "kept.pdf"
    result = tag_document(producer, out, prepare_analysis(producer), title="Notes", language="en-US")
    assert result.existing_tags.get("usable"), result.existing_tags
    with pikepdf.open(out) as pdf:
        findings, index = check_integrity(pdf)
        assert [f for f in findings if f.severity == "error"] == []
        notes = [e for e in index.elements if e.role == "Note"]
        assert len(notes) == 1                                            # no Note inside the Note
        pointing = [e for e in index.elements if "/Ref" in e.obj]
        assert pointing and all(r.objgen == notes[0].obj.objgen for e in pointing for r in e.obj.Ref)
