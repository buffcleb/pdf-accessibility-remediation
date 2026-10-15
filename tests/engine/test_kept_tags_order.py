"""Kept tags: put in reading order on slide-like pages; body text hidden as an artifact is tagged; untagged
drawings never become empty paragraphs."""
from __future__ import annotations

import pikepdf
from pikepdf import Array, Dictionary, Name

from app.pipeline.orchestrator import prepare_analysis
from app.tagging.tagger import tag_document
from app.verification.integrity import check_integrity
from app.verification.tagmap import build_tagmap


def _tagged(tmp_path, name, size, pieces, order, ext=None):
    """A page of `pieces` [(content ops, marker)] where marker is ("P"|"H1"…) for tagged content (one element each,
    in `order`), ("artifact", props) for an artifact section, or None for untagged-but-marked content (an MCID no
    element refers to)."""
    pdf = pikepdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica))
    ops, mcid, tagged = [], 0, []
    for content, marker in pieces:
        if isinstance(marker, tuple):
            ops.append(f"/Artifact {marker[1]} BDC {content} EMC")
        else:
            ops.append(f"/{marker or 'P'} <</MCID {mcid}>> BDC {content} EMC")
            tagged.append((mcid, marker))
            mcid += 1
    resources = Dictionary(Font=Dictionary(F1=font))
    if ext:
        resources.ExtGState = Dictionary({f"/{k}": Dictionary(v) for k, v in ext.items()})
    pdf.pages.append(pikepdf.Page(Dictionary(Type=Name.Page, MediaBox=[0, 0, *size],
                                             Resources=resources,
                                             Contents=pdf.make_stream("\n".join(ops).encode()))))
    pg = pdf.pages[0].obj
    pg.StructParents = 0
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    doc = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Document, P=root, Pg=pg))
    slide = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Sect, P=doc, Pg=pg))
    elems, parents = {}, []
    for m, role in tagged:
        if role is None:
            parents.append(None)
            continue
        e = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name("/" + role), P=slide, Pg=pg, K=m))
        elems[m] = e
        parents.append(e)
    slide.K = Array([elems[m] for m in order])
    doc.K = Array([slide])
    root.K = Array([doc])
    root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array([0, Array(parents)])))
    pdf.Root.StructTreeRoot = root
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    src = tmp_path / f"{name}.pdf"
    pdf.save(src)
    return src


def _text(x, y, s, size=14):
    return f"BT /F1 {size} Tf {x} {y} Td ({s}) Tj ET"


def _slide_pieces(h):
    return [(_text(60, h - 60, "Quarterly Roadmap", 28), "H1"),
            (_text(60, h - 140, "First, the items delivered this quarter for students."), "P"),
            (_text(60, h - 200, "Second, the items now underway for staff and faculty."), "P"),
            (_text(60, h - 260, "Third, what comes next year for every campus."), "P")]


def _order(path):
    return [e["text"][:5] for e in build_tagmap(path)["elements"] if e.get("own") and e.get("text")]


def test_a_slides_kept_order_follows_its_reading_order(tmp_path):
    # PowerPoint's order: the body, then the title
    src = _tagged(tmp_path, "slide", (960, 540), _slide_pieces(540), order=[1, 2, 3, 0])
    out = tmp_path / "o.pdf"
    res = tag_document(src, out, prepare_analysis(src), title="T", language="en-US")
    assert res.containers_reordered == 1
    assert _order(out) == ["Quart", "First", "Secon", "Third"]
    with pikepdf.open(out) as pdf:
        assert not [f for f in check_integrity(pdf)[0] if f.severity == "error"]


def test_a_portrait_pages_kept_order_is_left_alone(tmp_path):
    src = _tagged(tmp_path, "portrait", (612, 792), _slide_pieces(792), order=[1, 2, 3, 0])
    out = tmp_path / "o.pdf"
    res = tag_document(src, out, prepare_analysis(src), title="T", language="en-US")
    assert res.containers_reordered == 0 and _order(out) == ["First", "Secon", "Third", "Quart"]


def test_body_text_hidden_as_an_artifact_is_tagged(tmp_path):
    pieces = _slide_pieces(540) + [
        (_text(60, 300, "Release notes for every product are published each quarter on the calendar."),
         ("artifact", "<</Type /Layout>>")),
        (_text(60, 20, "Draft for comment only", 9), ("artifact", "<</Type /Pagination>>")),
        ("3 Tr " + _text(60, 360, "An invisible text layer that should stay hidden from readers."),
         ("artifact", "<</Type /Layout>>")),
    ]
    src = _tagged(tmp_path, "hidden", (960, 540), pieces, order=[0, 1, 2, 3])
    out = tmp_path / "o.pdf"
    res = tag_document(src, out, prepare_analysis(src), title="T", language="en-US")
    assert res.artifacts_released == 1
    texts = [e.get("text") or "" for e in build_tagmap(out)["elements"] if e.get("own")]
    assert any(t.startswith("Release notes") for t in texts)
    assert not any("Confidential" in t or "invisible" in t for t in texts)


def test_an_untagged_drawing_is_an_artifact_not_an_empty_paragraph(tmp_path):
    pieces = _slide_pieces(540) + [("0 0 1 RG 2 w 50 360 m 900 360 l S 50 350 200 20 re S", None)]
    src = _tagged(tmp_path, "drawing", (960, 540), pieces, order=[0, 1, 2, 3])
    out = tmp_path / "o.pdf"
    res = tag_document(src, out, prepare_analysis(src), title="T", language="en-US")
    assert res.orphans_artifacted == 1
    assert not [e for e in build_tagmap(out)["elements"] if e["role"] in ("P", "H1", "H2", "H3") and not e.get("text")]


def test_a_label_hidden_under_a_shape_drawn_over_it_is_not_read(tmp_path):
    """PowerPoint leaves an old tab label under the shape of the new tab, and tags both."""
    tab = "0.6 0.9 0.6 rg 50 400 120 30 re f"
    pieces = _slide_pieces(540) + [
        (_text(70, 410, "Now"), "P"),                                       # the old label...
        (tab, ("artifact", "<</Type /Layout>>")),                           # ...covered by the new tab's shape
        (_text(66, 410, "New"), "P"),                                       # the label shown on it
        (_text(70, 300, "Later"), "P"),
        ("q /Half gs 1 1 1 rg 50 290 120 30 re f Q", ("artifact", "<</Type /Layout>>")),  # see-through: still read
        ("3 Tr " + _text(70, 200, "Layer"), "P"),
        ("1 1 1 rg 50 190 120 30 re f", ("artifact", "<</Type /Layout>>")),  # invisible text: real content, kept
    ]
    src = _tagged(tmp_path, "tabs", (960, 540), pieces, order=[0, 1, 2, 3, 4, 5, 6, 7], ext={"Half": {"/ca": 0.5}})
    out = tmp_path / "o.pdf"
    res = tag_document(src, out, prepare_analysis(src), title="T", language="en-US")
    texts = [e.get("text") or "" for e in build_tagmap(out)["elements"] if e.get("own")]
    assert res.hidden_text_artifacted == 1
    assert "Now" not in texts and {"New", "Later", "Layer"} <= set(texts)
    with pikepdf.open(out) as pdf:
        assert not [f for f in check_integrity(pdf)[0] if f.severity == "error"]


def test_marks_explained_by_a_legend_get_its_label_and_sit_before_their_item(tmp_path):
    """A slide's legend ("▲ Beta  ◆ Select") explains the marks beside items: each mark's alt text is the label (no
    AI, which would call a small triangle decorative), and it's read just before its item."""
    tri = lambda x, y: f"0.3 0.6 1 rg {x} {y} m {x + 12} {y} l {x + 6} {y + 11} l f"           # noqa: E731
    dia = lambda x, y: f"1 0.8 0 rg {x + 6} {y} m {x + 12} {y + 6} l {x + 6} {y + 12} l {x} {y + 6} l f"  # noqa: E731
    pieces = _slide_pieces(540) + [
        (tri(700, 500) + " " + _text(716, 502, "Beta", 9) + " " + dia(760, 499) + " " + _text(776, 502, "Select", 9),
         ("artifact", "<</Type /Layout>>")),                         # the legend
        (tri(40, 397), "Figure"),                                    # the mark beside the first item...
        (dia(40, 337), "Figure"),                                    # ...and beside the second
    ]
    # Items at x 60: "First…" at y 400 (h-140), "Second…" at 340 (h-200); the tree reads the marks last
    src = _tagged(tmp_path, "legend", (960, 540), pieces, order=[0, 1, 2, 3, 4, 5])
    out = tmp_path / "o.pdf"
    res = tag_document(src, out, prepare_analysis(src), title="T", language="en-US")
    assert res.legend_marks == 2 and not res.figures_needing_alt
    els = [e for e in build_tagmap(out)["elements"] if e.get("own")]
    seq = [e.get("alt") if e["role"] == "Figure" else (e.get("text") or "")[:5] for e in els]
    assert seq == ["Quart", "Beta", "First", "Select", "Secon", "Third"], seq
