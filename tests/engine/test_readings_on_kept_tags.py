"""A document that arrives tagged keeps its tags, and a reading of its notation (54 of them on a 13-page chemistry
test, all lost) is written onto the kept tag that holds the same content."""
import pikepdf
from pikepdf import Array, Dictionary, Name

from app.pipeline.orchestrator import prepare_analysis
from app.tagging.tagger import tag_document
from app.verification.integrity import check_integrity
from tests.engine.pdfs import one_page


def _tagged(path):
    """Two paragraphs, each its own P; the second also holds a Link, so it has something of its own."""
    content = [b"/P <</MCID 0>> BDC BT /F1 12 Tf 72 700 Td (The acid H2SO4 is added slowly.) Tj ET EMC",
               b"/P <</MCID 1>> BDC BT /F1 12 Tf 72 640 Td (Rinse with H2O and dry.) Tj ET EMC",
               b"/P <</MCID 2>> BDC BT /F1 12 Tf 72 580 Td (Nothing to read here.) Tj ET EMC"]
    pdf, page = one_page(None, content)
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    doc = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Document, P=root))
    ps = [pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.P, P=doc, Pg=page, K=i)) for i in range(3)]
    ps[1].Alt = pikepdf.String("the author's own words for this line")
    doc.K, root.K = Array(ps), Array([doc])
    root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array([0, Array(ps)])))
    page.StructParents = 0
    pdf.Root.StructTreeRoot = root
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    pdf.save(path)
    return [p.objgen for p in ps]


def test_a_reading_is_written_onto_the_kept_tag_with_the_same_content(tmp_path):
    src, out = tmp_path / "in.pdf", tmp_path / "out.pdf"
    _tagged(src)
    analysis = prepare_analysis(src)
    blocks = sorted((b for b in analysis.pages[0].blocks if b.role != "Artifact"), key=lambda b: b.bbox[1])
    assert len(blocks) == 3
    blocks[0].actual_text, blocks[0].notation_reading = "The acid H 2 S O 4 is added slowly.", True
    blocks[1].actual_text, blocks[1].notation_reading = "Rinse with H 2 O and dry.", True
    blocks[2].actual_text = "a reading the layout rules made, not a notation reading"

    result = tag_document(src, out, analysis, title="Acid", language="en-US", pdfua_part=1)
    assert (result.readings_on_kept_tags, result.readings_unplaced) == (1, 1)
    with pikepdf.open(out) as pdf:
        findings, _ = check_integrity(pdf)
        assert [f for f in findings if f.severity == "error"] == []
        ps = [o for o in pdf.objects if isinstance(o, Dictionary) and o.get("/S") == Name.P]
        ps.sort(key=lambda o: int(o.K) if not isinstance(o.K, Array) else int(o.K[0]))
        assert str(ps[0].ActualText) == "The acid H 2 S O 4 is added slowly."
        # A tag that already says something of its own keeps it, and the report says a reading wasn't attached
        assert "/ActualText" not in ps[1] and "author's own words" in str(ps[1].Alt)
        assert "/ActualText" not in ps[2]
    assert any("could not be attached" in f for f in result.existing_tags["findings"])
