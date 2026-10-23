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
