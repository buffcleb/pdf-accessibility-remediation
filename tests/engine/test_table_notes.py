"""Notes under a table: a block right below it that starts with a symbol its cells carry becomes a Note; the cells
with the symbol say it as a footnote and point at the note."""
import pikepdf
import pymupdf as fitz

from app.pipeline.models import TableCell, TableRegion
from app.pipeline.orchestrator import prepare_analysis
from app.pipeline.structure import _say_table_marks
from app.tagging.tagger import tag_document
from app.verification.integrity import check_integrity
from app.verification.tagmap import build_tagmap

GRID = [["Drug", "Dose"], ["Amoxicillin", "500*"], ["Ibuprofen", "200"]]
X0, Y0, W, H = 72, 120, 120, 24


def _tagged(tmp_path, note="* Adjusted for weight in children under twelve.", note_y=None):
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 90), "Usual doses", fontsize=16, fontname="hebo")
    cells = []
    for r, row in enumerate(GRID):
        for c, text in enumerate(row):
            box = (X0 + W * c, Y0 + H * r, X0 + W * (c + 1), Y0 + H * (r + 1))
            page.insert_text((box[0] + 4, box[1] + 16), text, fontsize=11, fontname="helv")
            cells.append(TableCell(row=r, col=c, text=text, bbox=box))
    bottom = Y0 + H * len(GRID)
    page.insert_text((72, note_y or bottom + 20), note, fontsize=11, fontname="helv")
    page.insert_text((72, 600), "The rest of the page is ordinary prose about the doses above.", fontsize=11, fontname="helv")
    src = tmp_path / "doses.pdf"
    doc.save(src)
    analysis = prepare_analysis(src)
    pa = analysis.pages[0]
    # As the analysis does for a table it finds: the text inside is the table's
    pa.blocks = [b for b in pa.blocks if not (Y0 - 2 <= b.bbox[1] < bottom)]
    pa.tables = [TableRegion(id="p0t0", page=0, bbox=(X0, Y0, X0 + 2 * W, bottom), n_rows=3, n_cols=2, cells=cells)]
    pa.reading_order = [b.id for b in sorted(pa.blocks, key=lambda b: b.bbox[1]) if b.bbox[1] < Y0] + ["p0t0"] + \
        [b.id for b in sorted(pa.blocks, key=lambda b: b.bbox[1]) if b.bbox[1] >= bottom]
    from app.pipeline.structure import _mark_table_notes
    _mark_table_notes(analysis)   # (roles are assigned before the table is set here, so the step runs again)
    out = tmp_path / "doses_tagged.pdf"
    tag_document(src, out, analysis, title="Doses", language="en-US")
    return out, analysis


def test_a_note_under_a_table_is_linked_from_the_cells_with_its_symbol(tmp_path):
    out, analysis = _tagged(tmp_path)
    assert analysis.pages[0].tables[0].notes == {"*": next(b.id for b in analysis.pages[0].blocks if b.role == "Note")}
    with pikepdf.open(out) as pdf:
        findings, index = check_integrity(pdf)
        assert [f for f in findings if f.severity == "error"] == []
        note = next(e for e in index.elements if e.role == "Note")
        assert "/ID" in note.obj
        linked = [e for e in index.elements if "/Ref" in e.obj]
        assert [e.role for e in linked] == ["TD"] and [r.objgen for r in linked[0].obj.Ref] == [note.obj.objgen]
        assert str(linked[0].obj.ActualText) == "500 (footnote asterisk)"
    texts = {e.get("text"): e for e in build_tagmap(out)["elements"] if e["role"] in ("TD", "TH")}
    assert "refs" not in texts["200"]


def test_what_isnt_a_table_note(tmp_path):
    for kw in ({"note": "† A symbol none of the cells carry."}, {"note": "* Too far below the table to be its note.", "note_y": 400},
               {"note": "a Letters are left alone."}):
        _, analysis = _tagged(tmp_path, **kw)
        assert analysis.pages[0].tables[0].notes == {} and not any(b.role == "Note" for b in analysis.pages[0].blocks), kw


def test_how_a_cells_symbols_are_said():
    assert _say_table_marks("500*", {"*": "n"}) == "500 (footnote asterisk)"
    assert _say_table_marks("Dose† and **", {"†": "n"}) == "Dose (footnote dagger) and **"   # ** has no note
    assert _say_table_marks("*", {"*": "n"}) == "footnote asterisk"
