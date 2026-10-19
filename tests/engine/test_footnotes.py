"""Footnotes: small print at the foot of a page that starts with a mark raised after a word above it becomes a Note
with an ID, and the text with the mark names it (/Ref). Small numbered lines nothing points at stay as they were."""
import pikepdf
import pymupdf as fitz
import pytest
from pikepdf import Name

from app.pipeline.orchestrator import prepare_analysis
from app.tagging.editor import apply_edits
from app.tagging.tagger import tag_document
from app.verification.integrity import check_integrity
from app.verification.tagmap import build_tagmap

LINES = ["The survey covered forty sites across the region in spring",
         "and the results were consistent with earlier work on soils"]
NOTE = "Smith and Jones, Soils of the North, second edition, 2019."


def _page(tmp_path, *, mark="1", raised_lead=False, note=None, note_y=700, numbered=False, extra=None, mark_after=None):
    """Two lines of body text, the second ending in a raised mark, and a footnote at the foot of the page."""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    y = 100
    for line in LINES:
        page.insert_text((72, y), line, fontsize=11, fontname="helv")
        y += 16
    last = mark_after or LINES[1]
    if mark_after:
        page.insert_text((72, y), mark_after, fontsize=11, fontname="helv")
        y += 16
    if mark:
        page.insert_text((72 + fitz.get_text_length(last, "helv", 11), y - 16 - 4), mark, fontsize=7, fontname="helv")
    page.insert_text((72, y + 10), "Nothing else of note happened during the second visit to the coast.", fontsize=11,
                     fontname="helv")
    text = note if note is not None else NOTE
    lead = mark or "1"
    if raised_lead:
        page.insert_text((72, note_y - 3), lead, fontsize=5, fontname="helv")
        page.insert_text((76, note_y), text, fontsize=8, fontname="helv")
    else:
        page.insert_text((72, note_y), f"{lead}{'.' if numbered else ''} {text}", fontsize=8, fontname="helv")
    for x, yy, t in extra or []:
        page.insert_text((x, yy), t, fontsize=8, fontname="helv")
    src = tmp_path / "notes.pdf"
    doc.save(src)
    return src


def _tag(src, tmp_path):
    out = tmp_path / "notes_tagged.pdf"
    result = tag_document(src, out, prepare_analysis(src), title="Notes", language="en-US")
    return out, result


def _roles(path):
    return [(e["role"], (e.get("text") or "")[:12]) for e in build_tagmap(path)["elements"]][1:]


def test_a_footnote_becomes_a_note_with_an_id_that_the_text_with_its_mark_names(tmp_path):
    out, result = _tag(_page(tmp_path), tmp_path)
    assert result.footnotes == 1
    with pikepdf.open(out) as pdf:
        findings, index = check_integrity(pdf)
        assert [f for f in findings if f.severity == "error"] == []
        note = next(e for e in index.elements if e.role == "Note")
        assert bytes(note.obj.ID).startswith(b"note-")
        names = list(pdf.Root.StructTreeRoot.IDTree.Names)
        assert bytes(names[0]) == bytes(note.obj.ID) and names[1].objgen == note.obj.objgen
        # The mark is a Reference of its own inside its paragraph, and names the note
        pointing = [e for e in index.elements if "/Ref" in e.obj]
        assert len(pointing) == 1 and pointing[0].role == "Reference"
        assert index.elements[pointing[0].parent].role == "P"
        assert [r.objgen for r in pointing[0].obj.Ref] == [note.obj.objgen]


def test_a_raised_or_symbol_mark_and_a_numbered_footnote_are_found_too(tmp_path):
    for kw in ({"raised_lead": True}, {"mark": "*"}, {"numbered": True}):
        out, result = _tag(_page(tmp_path, **kw), tmp_path)
        assert result.footnotes == 1, kw
        assert [r for r, _ in _roles(out)].count("Note") == 1 and "LI" not in [r for r, _ in _roles(out)], kw


def test_small_numbered_lines_nothing_points_at_stay_as_they_were(tmp_path):
    # No raised mark in the text: the same small line at the foot is an ordinary paragraph
    out, result = _tag(_page(tmp_path, mark=None), tmp_path)
    assert result.footnotes == 0 and "Note" not in [r for r, _ in _roles(out)]
    # A mark, but the small print is a value or a few words: not a note
    out, result = _tag(_page(tmp_path, note="12.5 mm"), tmp_path)
    assert result.footnotes == 0
    # A raised number after a number is a power ("10²"), not a mark
    out, result = _tag(_page(tmp_path, mark="2", mark_after="The area was about 10"), tmp_path)
    assert result.footnotes == 0
    # Small print higher up the page, above the mark, isn't a footnote
    out, result = _tag(_page(tmp_path, note_y=60), tmp_path)
    assert result.footnotes == 0


def test_a_kept_note_without_an_id_is_given_one(tmp_path):
    out, _ = _tag(_page(tmp_path), tmp_path)
    bare = tmp_path / "bare.pdf"
    with pikepdf.open(out) as pdf:  # as a producer might write it: a Note with no ID, and no IDTree
        for e in check_integrity(pdf)[1].elements:
            if e.role == "Note":
                del e.obj["/ID"]
        del pdf.Root.StructTreeRoot["/IDTree"]
        pdf.save(bare)
    again = tmp_path / "again.pdf"
    result = tag_document(bare, again, prepare_analysis(bare), title="Notes", language="en-US")
    assert result.existing_tags["present"] and result.notes_given_ids == 1
    with pikepdf.open(again) as pdf:
        notes = [e for e in check_integrity(pdf)[1].elements if e.role == "Note"]
        assert len(notes) == 1 and "/ID" in notes[0].obj
        assert bytes(notes[0].obj.ID) in {bytes(k) for k in list(pdf.Root.StructTreeRoot.IDTree.Names)[::2]}


def test_a_paragraph_changed_to_a_note_in_the_tag_viewer_is_given_an_id(tmp_path):
    out, _ = _tag(_page(tmp_path, mark=None), tmp_path)
    p = next(e for e in build_tagmap(out)["elements"] if e["role"] == "P" and (e.get("text") or "").startswith("1 "))
    dst = tmp_path / "edited.pdf"
    apply_edits(out, dst, [{"op": "set_role", "element": p["i"], "expect_role": "P", "role": "Note"}])
    with pikepdf.open(dst) as pdf:
        findings, index = check_integrity(pdf)
        assert [f for f in findings if f.severity == "error"] == []
        note = index.elements[p["i"]].obj
        assert note.S == Name.Note and "/ID" in note
        assert bytes(note.ID) in {bytes(k) for k in list(pdf.Root.StructTreeRoot.IDTree.Names)[::2]}


def test_a_footnote_number_set_apart_by_a_gap_is_joined_to_its_note(tmp_path):
    src = _page(tmp_path)
    # Redraw the foot of the page: a small raised number, then the note's words well to its right
    doc = fitz.open(src)
    page = doc[0]
    page.add_redact_annot(fitz.Rect(60, 680, 560, 720))
    page.apply_redactions()
    page.insert_text((72, 697), "1", fontsize=5, fontname="helv")
    page.insert_text((86, 700), NOTE, fontsize=8, fontname="helv")
    gapped = tmp_path / "gapped.pdf"
    doc.save(gapped)
    out, result = _tag(gapped, tmp_path)
    assert result.footnotes == 1
    notes = [e for e in build_tagmap(out)["elements"] if e["role"] == "Note"]
    assert len(notes) == 1 and notes[0]["text"].startswith("1")         # the number reads with its note
    # the number is not a tag of its own (the only "1" tagged apart is the mark in the text, a Reference)
    assert [e["role"] for e in build_tagmap(out)["elements"] if (e.get("text") or "").strip() == "1"] == ["Reference"]


def _pixels(path):
    with fitz.open(path) as doc:
        return [p.get_pixmap(dpi=60).samples for p in doc]


def test_a_mark_with_a_footnote_is_said_as_one(tmp_path):
    for kw, said in (({}, "footnote 1"), ({"mark": "*"}, "footnote asterisk")):
        src = _page(tmp_path, **kw)
        out, result = _tag(src, tmp_path)
        assert result.footnote_references == 1 and _pixels(out) == _pixels(src), kw   # marked apart, drawn the same
        with pikepdf.open(out) as pdf:
            index = check_integrity(pdf)[1]
            ref = next(e for e in index.elements if e.role == "Reference")
            assert str(ref.obj.ActualText) == said, kw
            assert "/ActualText" not in index.elements[ref.parent].obj, kw   # the words read as drawn


def test_a_paragraph_that_needs_its_own_reading_says_the_mark_in_it(tmp_path):
    """A paragraph with a power in it keeps its read-aloud text ("10 squared"), which covers its Reference too: the
    footnote is said in that text."""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "The survey covered forty sites across the region in spring", fontsize=11, fontname="helv")
    page.insert_text((72, 116), "and measured over 10", fontsize=11, fontname="helv")
    x = 72 + fitz.get_text_length("and measured over 10", "helv", 11)
    page.insert_text((x, 112), "2", fontsize=7, fontname="helv")                       # 10², a power
    page.insert_text((x + 6, 116), " metres of the soils", fontsize=11, fontname="helv")
    x = x + 6 + fitz.get_text_length(" metres of the soils", "helv", 11)
    page.insert_text((x, 112), "1", fontsize=7, fontname="helv")                       # soils¹, a footnote mark
    page.insert_text((72, 700), "1 " + NOTE, fontsize=8, fontname="helv")
    powered = tmp_path / "powered.pdf"
    doc.save(powered)
    out, result = _tag(powered, tmp_path)
    assert result.footnotes == 1
    with pikepdf.open(out) as pdf:
        index = check_integrity(pdf)[1]
        p = next(e for e in index.elements if e.role == "P" and "/ActualText" in e.obj and "soils" in str(e.obj.ActualText))
        assert "10 squared" in str(p.obj.ActualText) and "soils (footnote 1)" in str(p.obj.ActualText)
        refs = [e for e in index.elements if e.role == "Reference"]
        assert all("/ActualText" not in e.obj for e in refs)


def test_only_the_raised_mark_is_said_as_a_footnote_and_only_when_it_has_one():
    from app.pipeline.models import TextBlock
    from app.pipeline.structure import _say_footnote_marks

    b = TextBlock(id="b", page=0, bbox=(0, 0, 1, 1), text="Fit R1 as in the tests on soils1 and soils2 here.",
                  font_size=11, marks=["1", "2"], mark_after=["soils", "soils"])
    _say_footnote_marks(b, {"1"})        # only footnote 1 is on the page
    assert b.actual_text == "Fit R1 as in the tests on soils (footnote 1) and soils2 here."
    untouched = TextBlock(id="c", page=0, bbox=(0, 0, 1, 1), text="On soils1.", font_size=11, marks=["1"],
                          mark_after=["soils"])
    _say_footnote_marks(untouched, set())
    assert untouched.actual_text is None


def test_text_can_be_pointed_at_a_note_by_hand_and_unpointed(tmp_path):
    from app.tagging.editor import EditError, validate_ops

    out, _ = _tag(_page(tmp_path, mark=None), tmp_path)   # no raised mark: the rules link nothing
    tm = build_tagmap(out)
    body = next(e for e in tm["elements"] if e["role"] == "P" and (e.get("text") or "").startswith("The survey"))
    foot = next(e for e in tm["elements"] if e["role"] == "P" and (e.get("text") or "").startswith("1 "))
    as_note = tmp_path / "note.pdf"
    apply_edits(out, as_note, [{"op": "set_role", "element": foot["i"], "expect_role": "P", "role": "Note"}])
    linked = tmp_path / "linked.pdf"
    log = apply_edits(as_note, linked, [{"op": "link_note", "element": body["i"], "expect_role": "P", "note": foot["i"]}])
    assert "points at note" in log[0]
    after = build_tagmap(linked)
    assert after["elements"][body["i"]]["refs"] == [foot["i"]]
    with pikepdf.open(linked) as pdf:
        findings, index = check_integrity(pdf)
        assert [f for f in findings if f.severity == "error"] == [] and "/ID" in index.elements[foot["i"]].obj
    unlinked = tmp_path / "unlinked.pdf"
    apply_edits(linked, unlinked, [{"op": "link_note", "element": body["i"], "expect_role": "P", "note": None}])
    assert "refs" not in build_tagmap(unlinked)["elements"][body["i"]]
    # Only a note can be pointed at, and a note doesn't point at one
    with pytest.raises(EditError, match="isn't a note"):
        apply_edits(out, tmp_path / "x.pdf", [{"op": "link_note", "element": body["i"], "expect_role": "P", "note": foot["i"]}])
    with pytest.raises(EditError, match="can't point at a note"):
        apply_edits(as_note, tmp_path / "x.pdf", [{"op": "link_note", "element": foot["i"], "expect_role": "Note", "note": foot["i"] - 1}])
    for bad in ({"op": "link_note", "element": 3, "note": 3}, {"op": "link_note", "element": 3, "note": "4"}):
        with pytest.raises(EditError, match="a link needs the note"):
            validate_ops([bad])


def _list_page(tmp_path, nested=False):
    """A bulleted list whose second item ends in a raised mark (with sub-points under it, if nested), and the footnote
    at the foot of the page."""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 90), "The survey team brought the following equipment to every site:", fontsize=11,
                     fontname="helv")
    items = [(90, "- Two soil corers and a spare set of blades"), (90, "- A field balance accurate to a tenth of a gram"),
             (90, "- Sample bags labeled with the site and the date")]
    if nested:
        items[2:2] = [(110, "- one for wet soil"), (110, "- one for dry soil")]
    y = 112
    for x, text in items:
        page.insert_text((x, y), text, fontsize=11, fontname="helv")
        if "balance" in text:
            page.insert_text((x + fitz.get_text_length(text, "helv", 11), y - 4), "1", fontsize=7, fontname="helv")
        y += 18
    page.insert_text((72, y + 16), "Each site took about two hours to survey from start to finish.", fontsize=11,
                     fontname="helv")
    page.insert_text((72, 700), "1 Calibrated each morning against a 100 g reference weight.", fontsize=8,
                     fontname="helv")
    src = tmp_path / "list_notes.pdf"
    doc.save(src)
    return src


@pytest.mark.parametrize("nested", [False, True])
def test_a_mark_in_a_list_item_is_a_reference_of_its_own(tmp_path, nested):
    """#285: as in a paragraph, the mark is tagged apart inside the item, names the note, and says "footnote 1"."""
    src = _list_page(tmp_path, nested)
    out, result = _tag(src, tmp_path)
    assert result.footnotes == 1 and result.footnote_references == 1
    assert _pixels(out) == _pixels(src)                                   # marked apart, drawn the same
    with pikepdf.open(out) as pdf:
        findings, index = check_integrity(pdf)
        assert [f for f in findings if f.severity == "error"] == []
        note = next(e for e in index.elements if e.role == "Note")
        ref = next(e for e in index.elements if e.role == "Reference")
        assert [r.objgen for r in ref.obj.Ref] == [note.obj.objgen]
        assert str(ref.obj.ActualText) == "footnote 1"
        holder = index.elements[ref.parent]                                # the item's body (a Span, beside a sub-list)
        assert holder.role == ("Span" if nested else "LBody") and "/ActualText" not in holder.obj
        chain = []
        e = holder
        while e.parent is not None:
            e = index.elements[e.parent]
            chain.append(e.role)
        assert chain[:2] == (["LBody", "LI"] if nested else ["LI", "L"])


def _as_a_producer_tags_it(tagged, dst):
    """The footnote tagging undone, as many producers leave it: the note a plain paragraph with no ID, the mark part
    of its sentence (its Reference folded back in), nothing pointing anywhere."""
    with pikepdf.open(tagged) as pdf:
        root = pdf.Root.StructTreeRoot
        if "/IDTree" in root:
            del root["/IDTree"]

        def walk(e):
            kids = e.get("/K")
            if isinstance(kids, pikepdf.Array):
                out = []
                for k in kids:
                    if isinstance(k, pikepdf.Dictionary) and k.get("/S") == Name.Reference:
                        inner = k.K
                        out += list(inner) if isinstance(inner, pikepdf.Array) else [inner]
                        continue
                    if isinstance(k, pikepdf.Dictionary) and "/S" in k:
                        walk(k)
                    out.append(k)
                e.K = pikepdf.Array(out)
            elif isinstance(kids, pikepdf.Dictionary) and "/S" in kids:
                walk(kids)
            if e.get("/S") == Name.Note:
                e.S = Name.P
            for key in ("/ID", "/Ref"):
                if key in e:
                    del e[key]
        walk(root)
        # the folded marks' parent tree entries now name the paragraph
        pt = root.ParentTree.Nums
        for i in range(1, len(pt), 2):
            arr = pt[i]
            if isinstance(arr, pikepdf.Array):
                for j, x in enumerate(arr):
                    if isinstance(x, pikepdf.Dictionary) and x.get("/S") == Name.Reference:
                        arr[j] = x.P
        pdf.save(dst)
    return dst


def test_footnotes_reach_the_documents_own_tags_when_they_are_kept(tmp_path):
    """#285: a document that arrives tagged, its footnote a plain paragraph: the kept paragraph becomes a Note with an
    ID, and the kept paragraph with the mark names it and says the mark."""
    first, _ = _tag(_page(tmp_path), tmp_path)
    src = _as_a_producer_tags_it(first, tmp_path / "producer.pdf")
    assert [r for r, _ in _roles(src)].count("Note") == 0 and "Reference" not in [r for r, _ in _roles(src)]
    out = tmp_path / "kept.pdf"
    result = tag_document(src, out, prepare_analysis(src), title="Notes", language="en-US")
    assert result.existing_tags.get("usable"), result.existing_tags     # the document's own tags were kept
    assert result.kept_notes == 1 and result.kept_note_refs == 1
    with pikepdf.open(out) as pdf:
        findings, index = check_integrity(pdf)
        assert [f for f in findings if f.severity == "error"] == []
        note = next(e for e in index.elements if e.role == "Note")
        assert bytes(note.obj.ID).startswith(b"note-")                    # given its ID as any kept Note is
        pointing = [e for e in index.elements if "/Ref" in e.obj]
        assert len(pointing) == 1 and pointing[0].role == "P"
        assert [r.objgen for r in pointing[0].obj.Ref] == [note.obj.objgen]
        assert "soils (footnote 1)" in str(pointing[0].obj.ActualText)


def test_a_kept_footnote_the_producer_already_tagged_is_left_alone(tmp_path):
    first, _ = _tag(_page(tmp_path), tmp_path)                           # our own tags: Note, Reference, /Ref
    out = tmp_path / "again.pdf"
    result = tag_document(first, out, prepare_analysis(first), title="Notes", language="en-US")
    assert result.kept_notes == 0 and result.kept_note_refs == 0
    with pikepdf.open(out) as pdf:
        index = check_integrity(pdf)[1]
        assert [e.role for e in index.elements if "/Ref" in e.obj] == ["Reference"]
