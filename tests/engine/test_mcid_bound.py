"""Kept tags: a marked-content id far past the number of sequences on its page. The ParentTree names each
sequence's tag at the index of its id, so a page's array is as long as its largest id; a file with one id in the
millions used to have the tagger build an array that long."""
import pikepdf
from pikepdf import Array, Dictionary, Name

from app.pipeline.orchestrator import prepare_analysis
from app.tagging.tagger import tag_document
from app.verification.integrity import check_integrity
from tests.engine.pdfs import one_page

ABSURD = 3_000_000  # enough to show the problem on main in seconds, small enough for a test machine


def _tagged(path, ids):
    lines = ["Dosing", "Give the first dose in the morning.", "Check the level after five days."]
    content = [f"/P <</MCID {mcid}>> BDC BT /F1 12 Tf 72 {700 - 30 * i} Td ({t}) Tj ET EMC".encode()
               for i, (mcid, t) in enumerate(zip(ids, lines))]
    pdf, page = one_page(None, content)
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    doc = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Document, P=root))
    elems = [pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name(r), P=doc, Pg=page, K=mcid))
             for r, mcid in zip(("/H1", "/P", "/P"), ids)]
    doc.K = Array(elems)
    root.K = Array([doc])
    # The file's own parent tree is deliberately short: the tagger always regenerates it
    root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array([0, Array(elems)])))
    page.StructParents = 0
    pdf.Root.StructTreeRoot = root
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    pdf.save(path)


def _run(tmp_path, ids):
    src, out = tmp_path / "in.pdf", tmp_path / "out.pdf"
    _tagged(src, ids)
    result = tag_document(src, out, prepare_analysis(src), title="Dosing", language="en-US", pdfua_part=1)
    with pikepdf.open(out) as pdf:
        findings, index = check_integrity(pdf)
        assert [f for f in findings if f.severity == "error"] == []
        nums = list(pdf.Root.StructTreeRoot.ParentTree.Nums)
        longest = max(len(nums[i + 1]) for i in range(0, len(nums), 2))
    return result, longest


def test_an_absurd_id_has_the_tree_rebuilt_with_a_short_parent_tree(tmp_path):
    result, longest = _run(tmp_path, [0, 1, ABSURD])
    assert result.existing_tags["rebuilt_tree"] and "can't be kept" in result.existing_tags["reason"]
    assert longest == 3


def test_ordinary_ids_keep_the_tree(tmp_path):
    result, longest = _run(tmp_path, [0, 1, 7])  # a small gap is nothing unusual
    assert not result.existing_tags["rebuilt_tree"] and result.existing_tags["retained"] + result.existing_tags["repaired"] >= 3
    assert longest == 8
