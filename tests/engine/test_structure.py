from app.pipeline.orchestrator import prepare_analysis


def test_two_column_reading_order(samples):
    page = prepare_analysis(samples / "two_column.pdf").pages[0]
    assert page.columns == 2
    texts = [page.element(e).text[:22] for e in page.reading_order]
    assert texts[0].startswith("Two Column Article")
    col1 = [i for i, t in enumerate(texts) if t.startswith("Column 1")]
    col2 = [i for i, t in enumerate(texts) if t.startswith("Column 2")]
    assert max(col1) < min(col2), texts


def test_headings_and_lists(samples):
    a = prepare_analysis(samples / "simple_report.pdf")
    roles = {b.text[:20]: b.role for p in a.pages for b in p.blocks}
    assert roles["Annual Accessibility"] == "H1"
    assert roles["Section 1: Overview"] == "H2"
    assert sum(1 for r in roles.values() if r == "LI") >= 3


def test_table_grid_rebuilt_from_geometry():
    from app.pipeline.analyzer import rebuild_grid
    from app.pipeline.models import TableRegion

    raw = [
        # header row; a 5pt double-border sliver between the columns
        ((0, 0, 100, 20), "Week"), ((100, 0, 105, 20), ""), ((105, 0, 300, 20), "Topic"),
        # row 1: a cell spanning both columns (and the sliver)
        ((0, 20, 300, 40), "Intro"),
        # row 2 + wrapped continuation line split into its own row by the finder
        ((0, 40, 100, 60), "3"), ((105, 40, 300, 60), "Newton's laws,"), ((105, 60, 300, 75), "momentum"),
    ]
    cells, n_rows, n_cols = rebuild_grid(raw)
    assert (n_rows, n_cols) == (3, 2)
    by = {(c.row, c.col): c for c in cells}
    assert by[(0, 0)].text == "Week" and by[(0, 1)].text == "Topic"
    assert by[(1, 0)].col_span == 2
    assert by[(2, 1)].text == "Newton's laws, momentum"
    t = TableRegion("t", 0, (0, 0, 300, 75), n_rows, n_cols, cells)
    for r in range(n_rows):
        assert sum(c.col_span for c in t.normalized_cells() if c.row == r) == n_cols


def test_first_heading_in_tree_is_h1_after_reordering(samples, tmp_path):
    import pikepdf

    from app.tagging.tagger import tag_document

    a = prepare_analysis(samples / "simple_report.pdf")
    page = a.pages[0]
    # Simulate an AI reading order that puts the H2 before the H1.
    h1 = next(b.id for b in page.blocks if b.role == "H1")
    h2 = next(b.id for b in page.blocks if b.role == "H2")
    order = page.reading_order
    i1, i2 = order.index(h1), order.index(h2)
    order[i1], order[i2] = order[i2], order[i1]
    out = tmp_path / "o.pdf"
    res = tag_document(samples / "simple_report.pdf", out, a, title="T", language="en-US")
    assert res.headings_renumbered >= 1
    from app.validation.audit import iter_struct_elems
    with pikepdf.open(out) as pdf:
        heads = [str(e.S) for e in iter_struct_elems(pdf.Root.StructTreeRoot) if str(e.S)[1:2] == "H"]
    assert heads[0] == "/H1"
