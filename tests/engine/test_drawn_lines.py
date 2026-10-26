"""#438: a line drawn with characters (a dashed box border typed as a row of hyphens, a rule of underscores) is
decoration, not a formula, heading or paragraph; a table of contents line with dot leaders stays text."""
import pymupdf as fitz

from app.pipeline.orchestrator import prepare_analysis


def test_lines_drawn_with_characters_are_decoration(tmp_path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 80), "Abbreviations", fontsize=16)
    page.insert_text((72, 120), "- - - - - - - - - - - - - - · - - - - - - - - - - - -", fontsize=20)
    page.insert_text((72, 150), "ACS: acute coronary syndrome; UA: unstable angina", fontsize=9)
    page.insert_text((72, 190), "________________________________", fontsize=12)
    page.insert_text((72, 230), "Chapter one .................................. 4", fontsize=11)
    src = tmp_path / "lines.pdf"
    doc.save(src)
    blocks = prepare_analysis(src).pages[0].blocks
    role = {b.text.strip()[:12]: (b.role, b.artifact_type) for b in blocks}
    dashes = next(v for k, v in role.items() if k.startswith("- -"))
    rule = next(v for k, v in role.items() if k.startswith("___"))
    assert dashes == ("Artifact", "Layout") and rule == ("Artifact", "Layout")
    assert next(v for k, v in role.items() if k.startswith("ACS"))[0] == "P"
    assert next(v for k, v in role.items() if k.startswith("Chapter"))[0] != "Artifact"
    assert not any(b.role == "Formula" for b in blocks)


def test_a_line_with_a_word_is_text_however_long_its_leaders(tmp_path):
    """#634: a contents entry with long dot leaders, and a form's label with the line to write on, have more dots or
    underscores than letters, and are still text."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 80), "Introduction " + "." * 60 + " 2", fontsize=11)
    page.insert_text((72, 120), "Name: " + "_" * 50, fontsize=11)
    page.insert_text((72, 160), "." * 60, fontsize=11)
    page.insert_text((72, 200), "Chapter Two " + "." * 50 + " 9", fontsize=18)   # in a heading's size
    page.insert_text((72, 240), "x" + "-" * 120 + " Fig", fontsize=11)          # a word lost in a long rule
    src = tmp_path / "leaders.pdf"
    doc.save(src)
    role = {b.text.strip()[:6]: b.role for b in prepare_analysis(src).pages[0].blocks}
    assert role["Introd"] != "Artifact" and role["Name: "] != "Artifact"
    assert role["......"] == "Artifact"                                    # a rule of dots alone still is
    assert role["Chapte"] == "P"                                            # a contents entry, not a heading
    assert role["x-----"] == "Artifact"
