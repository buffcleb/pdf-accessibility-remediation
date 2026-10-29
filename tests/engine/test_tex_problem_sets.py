"""A TeX problem set saved through macOS Quartz (production job 9d0e9614): the fonts' own maps gave pi as an arrow
and the minus sign as U+0000, so two pages were flattened; a tall radical split problem 2, whose rest joined problem
1; parts "(c)" wrapped to a line's start became one-item lists; graph labels beside the drawing were read as
paragraphs; the title was the source file's name, "ps1"."""
import pikepdf
import pymupdf as fitz
from pikepdf import Array, Dictionary, Name

from app.pipeline.analyzer import _merge_units, _Run, analyze_page
from app.pipeline.orchestrator import JobOptions, _pick_title, prepare_analysis
from app.tagging.font_unicode import _cmap, add_missing_tounicode, parse_tounicode, wrong_entry
from tests.engine.layout import block, document, page


def test_a_fonts_wrong_map_entries_give_way_to_its_glyph_names(tmp_path):
    pdf = pikepdf.new()
    pdf.add_blank_page()
    names = ["/pi", "/minus", "/ff", "/quoteright", "/lscript", "/Delta"]
    own = {33: "⇡", 34: "\x00", 35: "ff", 36: "'", 37: "`", 38: "\x00"}
    font = pdf.make_indirect(Dictionary(
        Type=Name.Font, Subtype=Name.Type1, BaseFont=Name("/XWJNGX+CMMI10"),
        Encoding=Dictionary(Type=Name.Encoding, Differences=Array([33, *[Name(n) for n in names]])),
        ToUnicode=pdf.make_stream(_cmap(own))))
    pdf.pages[0].Resources = Dictionary(Font=Dictionary(F1=font))
    src, dst = tmp_path / "f.pdf", tmp_path / "o.pdf"
    pdf.save(src)
    (fixed,) = add_missing_tounicode(src, dst)
    assert "4 wrong entries replaced" in fixed and "existing map kept" in fixed
    with pikepdf.open(dst) as out:
        got = parse_tounicode(out.pages[0].Resources.Font.F1.ToUnicode.read_bytes())
    # pi was an arrow, minus and Delta nothing, lscript a grave accent; "ff" and a straight quote are left as written
    assert got == {33: "π", 34: "−", 35: "ff", 36: "'", 37: "ℓ", 38: "Δ"}


def test_what_counts_as_a_wrong_entry():
    assert wrong_entry("\x00", "−") and wrong_entry("﻿", "A") and wrong_entry("⇥", "×")
    assert wrong_entry("p", "√") and wrong_entry("↵", "ﬀ")
    assert not wrong_entry("ff", "ﬀ") and not wrong_entry("'", "’") and not wrong_entry("π", "π")
    assert not wrong_entry("-", "‐")  # hyphen and hyphen-minus: both punctuation
    assert not wrong_entry("\u00ad", "-")  # a soft hyphen for the hyphen at a line's end is meant


def _blocks(path):
    pa, _ = analyze_page(fitz.open(path), 0)
    return sorted((b for b in pa.blocks if b.role != "Artifact"), key=lambda b: (b.bbox[1], b.bbox[0]))


def _draw(path, lines):
    doc = fitz.open()
    p = doc.new_page(width=612, height=792)
    for x, y, text in lines:
        p.insert_text((x, y), text, fontsize=12, fontname="tiro")
    doc.save(path)
    return path


def test_parts_in_running_text_that_wrap_to_a_lines_start_stay_in_the_problem(tmp_path):
    first = "Carry out the following operations: (a) the sum of 756 and 37.2; (b) the product"
    width = fitz.get_text_length(first, fontname="tiro", fontsize=12)
    path = _draw(tmp_path / "parts.pdf", [
        (72, 200, "4."), (96, 200, first),
        (96, 214, "of 3.2 and 3.563, with its significant figures given correctly in each of the answers,"),
        (96, 228, "(c) the product 5.6 and 3.14; (d) the difference 425,991 and 425,987."),
        (72, 260, "5."), (96, 260, "A cyclist rides along a flat lane for 12 km at 18 km/hr."),
    ])
    assert width > 300
    texts = [b.text for b in _blocks(path)]
    assert texts[0].startswith("4. Carry out") and texts[0].endswith("425,987."), texts
    assert texts[1].startswith("5. A cyclist")


def test_parts_set_one_under_another_are_still_items(tmp_path):
    path = _draw(tmp_path / "under.pdf", [
        (72, 200, "4."), (96, 200, "Work out each of these:"),
        (96, 214, "(a) the sum of 756 and 37.2;"),
        (96, 228, "(b) the product of 3.2 and 3.563."),
    ])
    texts = [b.text for b in _blocks(path)]
    assert texts[-2:] == ["(a) the sum of 756 and 37.2;", "(b) the product of 3.2 and 3.563."], texts


def test_a_problems_number_and_its_first_part_on_one_line_are_one_item(tmp_path):
    path = _draw(tmp_path / "seven.pdf", [
        (72, 200, "7."), (96, 200, "(a) Is it possible for a ball at rest to have an acceleration?"),
        (72, 260, "8."), (96, 260, "An automobile increases its speed uniformly."),
    ])
    texts = [b.text for b in _blocks(path)]
    assert texts[0] == "7. (a) Is it possible for a ball at rest to have an acceleration?", texts


def test_a_tall_radical_set_apart_does_not_split_its_line():
    """MuPDF gave "2. ... T = 2π", the radical and "ℓ/g for the swing" as three blocks (the radical's 12 points
    wide gap kept the two pieces apart): they are one line of problem 2, and the radical is read in it."""
    page_ = fitz.open().new_page(width=612, height=792)

    def runs(text, x, base, top, bottom, width):
        span = {"font": "CMR10", "size": 12.0, "flags": 0, "color": 0}
        return [_Run(span, {"c": c, "bbox": (x + k * width, top, x + (k + 1) * width, bottom),
                            "origin": (x + k * width, base)}, k) for k, c in enumerate(text)]

    above = runs("volume of the room?", 96, 189, 179, 191, 6)
    left = runs("2. Later T = 2π", 300, 208, 198, 210, 6)
    radical = runs("√", 391, 196.6, 188, 232, 12)
    right = runs("ℓ/g for the time", 403, 208, 198, 210, 6)
    merged = _merge_units(page_, [(above, []), (left, []), (radical, []), (right, [])])
    assert len(merged) == 2 and len(merged[1][0]) == len(left) + len(radical) + len(right)
    assert radical[0].base == 208


def test_labels_beside_a_drawn_graph_are_part_of_it(tmp_path):
    doc = fitz.open()
    p = doc.new_page(width=612, height=792)
    p.insert_text((72, 100), "9. The chart below gives the speed of a skater at each second of a lap.",
                  fontsize=11, fontname="tiro")
    p.draw_rect(fitz.Rect(220, 160, 420, 240), color=(0, 0, 0), width=0.8)  # a bar chart in a frame
    for k, h in enumerate((30, 55, 70, 45, 60, 20)):
        p.draw_rect(fitz.Rect(225 + 32 * k, 240 - h, 245 + 32 * k, 240), color=(0, 0, 0), fill=(0.6, 0.6, 0.6))
    p.insert_text((200, 200), "vx", fontsize=9, fontname="tiro")      # left of the axis, outside the drawing
    p.insert_text((250, 252), "2  4  6  8  10  12", fontsize=9, fontname="tiro")  # under it
    p.insert_text((430, 244), "t (s)", fontsize=9, fontname="tiro")  # past its end
    p.insert_text((72, 300), "10. A train is initially at rest. It then starts moving to the right.", fontsize=11,
                  fontname="tiro")
    path = tmp_path / "graph.pdf"
    doc.save(path)
    pa, _ = analyze_page(fitz.open(path), 0)
    (fig,) = [i for i in pa.images if i.kind == "vector"]
    assert fig.bbox[0] <= 200 and fig.bbox[2] >= 440 and fig.bbox[3] >= 252
    assert [b.text[:3] for b in pa.blocks if b.role != "Artifact"] == ["9. ", "10."]


def test_a_title_that_is_the_source_files_name_gives_way_to_the_heading():
    a = document(page(block("b0", (254, 108, 358, 122), "Problem Set 1", "H1", size=14)), title="ps1")
    assert _pick_title(JobOptions(stem="physics"), a) == "Problem Set 1"
    # A name the first page prints is the document's
    form = document(page(block("b0", (72, 72, 300, 90), "Form W-9 Request for Taxpayer Identification", "H1")),
                    title="W-9")
    assert _pick_title(JobOptions(stem="fw9"), form) == "W-9"
    assert _pick_title(JobOptions(stem="x"), document(page(), title="Report v2.1")) == "Report v2.1"
