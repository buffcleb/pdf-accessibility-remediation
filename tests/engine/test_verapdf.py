from app.validation.verapdf import parse_report

SAMPLE = b"""<?xml version="1.0"?><report><jobs><job>
<validationReport profileName="PDF/UA-1 validation profile" isCompliant="false">
<details passedRules="105" failedRules="1" passedChecks="2087" failedChecks="2">
<rule specification="ISO 14289-1:2014" clause="7.21.4.1" testNumber="1" status="failed" failedChecks="2">
<description>Fonts shall be embedded</description>
<check status="failed"><context>root/document[0]/pages[3](6 0 obj PDPage)/contentStream[0]/operators[5]/font[0](Helvetica)</context></check>
</rule></details></validationReport></job></jobs></report>"""


def test_parse_report():
    r = parse_report(SAMPLE, "ua1")
    assert r.compliant is False and r.failed_rules == 1 and r.failed_checks == 2
    assert r.failures[0].clause == "7.21.4.1" and r.failures[0].pages == [3]
    assert r.pages_failing(("7.21",)) == {3}


WIDTHS = b"""<?xml version="1.0"?><report><jobs><job>
<validationReport profileName="PDF/UA-1 validation profile" isCompliant="false">
<details passedRules="105" failedRules="1" passedChecks="10" failedChecks="2">
<rule specification="ISO 14289-1:2014" clause="7.21.5" testNumber="1" status="failed" failedChecks="2">
<description>widths</description>
<check status="failed"><context>root/document[0]/pages[6](17 0 obj PDPage)/contentStream[0]/operators[837]/usedGlyphs[0](WCRCJZ+PazoMathBlackboardBold WCRCJZ+PazoMathBlackboardBold 82 0 2103569615 6 true)</context>
<errorMessage>Glyph width 785 in the embedded font program is not consistent with the Widths entry of the font dictionary (value 810)</errorMessage></check>
</rule></details></validationReport></job></jobs></report>"""


def test_parse_width_mismatches():
    r = parse_report(WIDTHS, "ua1")
    assert r.width_mismatches == {("WCRCJZ+PazoMathBlackboardBold", 82): (785.0, 810.0)}


def test_width_fix_is_pixel_identical(samples, tmp_path):
    """Correcting Widths plus TJ compensation must not move a single pixel."""
    import pikepdf
    import pymupdf as fitz

    from app.tagging.font_metrics import fix_width_mismatches

    # two_column.pdf uses simple base-14 fonts; give them explicit Widths so there is something to fix.
    src = tmp_path / "simple.pdf"
    with pikepdf.open(samples / "two_column.pdf") as pdf:
        for f in pdf.pages[0].obj.Resources.Font.values():
            ref = fitz.Font("helv")
            f.FirstChar, f.LastChar = 32, 126
            f.Widths = pikepdf.Array([round(ref.glyph_advance(c) * 1000) for c in range(32, 127)])
        pdf.save(src)
    with pikepdf.open(src) as pdf:
        font = next(iter(pdf.pages[0].obj.Resources.Font.values()))
        name, first, code = str(font.BaseFont)[1:], 32, ord("o")
        declared = float(font.Widths[code - first])
    out = tmp_path / "w.pdf"
    n = fix_width_mismatches(src, out, {(name, code): (declared - 25, declared)})
    assert n > 0
    a, b = fitz.open(src), fitz.open(out)
    assert a[0].get_pixmap(dpi=100).samples == b[0].get_pixmap(dpi=100).samples
    with pikepdf.open(out) as pdf:
        font = next(f for f in pdf.pages[0].obj.Resources.Font.values() if str(f.BaseFont)[1:] == name)
        assert float(font.Widths[code - first]) == declared - 25
