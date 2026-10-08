import pikepdf

from app.pipeline.orchestrator import prepare_analysis
from app.tagging.tagger import tag_document
from app.validation.audit import audit_pdf


def test_partial_tags_retained_repaired_replaced(samples, tmp_path):
    src = samples / "partially_tagged.pdf"
    analysis = prepare_analysis(src)
    for p in analysis.pages:
        for i in p.images:
            i.alt = "Bar chart of remediation progress"
    out = tmp_path / "p.pdf"
    res = tag_document(src, out, analysis, title="T", language="en-US")
    et = res.existing_tags

    assert et["present"] and et["usable"] and not et["rebuilt_tree"]
    assert et["retained"] >= 1          # Document element
    assert et["repaired"] == 3          # H3->H1, /MyPara role-mapped, Figure Alt added
    assert et["replaced"] == 1          # element pointing at a non-existent MCID
    reasons = " ".join(r for s in et["samples"] for r in s["reasons"])
    assert "H3 -> H1" in reasons and "/MyPara" in reasons and "Alt added" in reasons

    with pikepdf.open(out) as pdf:
        root = pdf.Root.StructTreeRoot
        assert str(root.RoleMap.MyPara) == "/P"
    a = audit_pdf(out)
    assert a["figures_missing_alt"] == 0
    assert a["content_tagged_ratio"] == 1.0  # untagged second paragraph was added


def test_unusable_tree_is_rebuilt(samples, tmp_path):
    """A tree whose references are mostly dangling is discarded and rebuilt."""
    src = tmp_path / "broken.pdf"
    with pikepdf.open(samples / "partially_tagged.pdf") as pdf:
        doc = pdf.Root.StructTreeRoot.K[0]
        for el in doc.K:
            el.K = pikepdf.Array([99])
        pdf.save(src)
    analysis = prepare_analysis(src)
    out = tmp_path / "rebuilt.pdf"
    res = tag_document(src, out, analysis, title="T", language="en-US")
    assert res.existing_tags["rebuilt_tree"] is True
    assert res.stripped_marks > 0
    assert audit_pdf(out)["content_tagged_ratio"] == 1.0


def _tagged_with(tmp_path, samples, build):
    """partially_tagged.pdf with its tree replaced by `build(pdf, page, doc_elem)`."""
    src = tmp_path / "custom.pdf"
    with pikepdf.open(samples / "partially_tagged.pdf") as pdf:
        doc = pdf.Root.StructTreeRoot.K[0]
        build(pdf, pdf.pages[0].obj, doc)
        pdf.save(src)
    return src


def test_inline_roles_map_to_span_and_list_items_are_repaired(samples, tmp_path):
    from pikepdf import Array, Dictionary, Name

    def build(pdf, pg, doc):
        mk = lambda s, parent, k: pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name(s), P=parent, Pg=pg, K=k))  # noqa: E731
        lst = mk("/L", doc, Array())
        li = mk("/LI", lst, Array())
        em = mk("/Em", li, 1)            # LI holding an /Em directly: invalid (7.2-20) and a custom role
        li.K = Array([em])
        lst.K = Array([li])
        doc.K = Array([mk("/H1", doc, 0), lst, mk("/Figure", doc, 2)])

    src = _tagged_with(tmp_path, samples, build)
    analysis = prepare_analysis(src)
    out = tmp_path / "o.pdf"
    res = tag_document(src, out, analysis, title="T", language="en-US")
    reasons = " ".join(r for s in res.existing_tags["samples"] for r in s["reasons"])
    assert "/Em mapped to /Span" in reasons
    assert "wrapped in LBody" in reasons
    with pikepdf.open(out) as pdf:
        doc = pdf.Root.StructTreeRoot.K[0]
        li = next(k for k in doc.K if str(k.S) == "/L").K[0]
        assert [str(k.S) for k in li.K] == ["/LBody"]


def test_links_in_retained_tree_get_descriptions(samples, tmp_path):
    from pikepdf import Array, Dictionary, Name

    src = tmp_path / "link.pdf"
    with pikepdf.open(samples / "partially_tagged.pdf") as pdf:
        pg = pdf.pages[0].obj
        annot = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link, Rect=Array([50, 700, 200, 720]),
                                             A=Dictionary(S=Name.URI, URI=pikepdf.String("https://example.com"))))
        pg.Annots = Array([annot])
        doc = pdf.Root.StructTreeRoot.K[0]
        link = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Link, P=doc, Pg=pg,
                                            K=Array([Dictionary(Type=Name.OBJR, Obj=annot, Pg=pg)])))
        doc.K.append(link)
        annot.StructParent = 5
        pdf.save(src)
    out = tmp_path / "o.pdf"
    tag_document(src, out, prepare_analysis(src), title="T", language="en-US")
    with pikepdf.open(out) as pdf:
        a = pdf.pages[0].obj.Annots[0]
        assert "example.com" in str(a.Contents)


def test_decorative_existing_figure_becomes_artifact(samples, tmp_path):
    from app.verification.integrity import check_integrity

    src = samples / "partially_tagged.pdf"
    analysis = prepare_analysis(src)
    img = analysis.pages[0].images[0]
    out = tmp_path / "deco.pdf"
    res = tag_document(src, out, analysis, title="T", language="en-US",
                       region_alts=[(0, img.bbox, None, True)])
    assert res.existing_tags["artifacted"] == 1 and res.figures_artifacted == 1
    with pikepdf.open(out) as pdf:
        findings, index = check_integrity(pdf)
        assert not [f for f in findings if f.severity == "error"]
        assert not any(e.role == "Figure" for e in index.elements)
    assert audit_pdf(out)["content_tagged_ratio"] == 1.0


def test_nested_existing_marked_content_forces_rebuild(samples, tmp_path):
    """Quartz 'Save as PDF' output nests tagged sections; such tags must be rebuilt, not kept."""
    from app.verification.integrity import check_integrity

    src = tmp_path / "nested.pdf"
    with pikepdf.open(samples / "partially_tagged.pdf") as pdf:
        page = pdf.pages[0]
        ops = list(pikepdf.parse_content_stream(page.obj))
        # drop the first EMC so the next MCID section opens inside the previous one
        first_emc = next(i for i, o in enumerate(ops) if isinstance(o, pikepdf.ContentStreamInstruction)
                         and str(o.operator) == "EMC")
        ops.pop(first_emc)
        ops.append(pikepdf.ContentStreamInstruction([], pikepdf.Operator("EMC")))
        page.obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(ops))
        pdf.save(src)
    out = tmp_path / "out.pdf"
    res = tag_document(src, out, prepare_analysis(src), title="T", language="en-US")
    assert res.existing_tags["rebuilt_tree"] and "nested" in res.existing_tags["reason"]
    with pikepdf.open(out) as pdf:
        findings, _ = check_integrity(pdf)
    assert not [f for f in findings if f.severity == "error"]


def test_stale_struct_parents_on_forms_are_cleared(samples, tmp_path):
    """Untagged exports (seen from Google) can carry leftover /StructParents on pages and forms."""
    import pymupdf as fitz

    from app.verification.integrity import check_integrity

    src = tmp_path / "stale.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.show_pdf_page(page.rect, fitz.open(samples / "table_doc.pdf"), 0)  # content drawn via a form XObject
    tmp = tmp_path / "tmp.pdf"
    doc.save(tmp)
    with pikepdf.open(tmp) as pdf:
        pg = pdf.pages[0].obj
        pg.StructParents = 0
        for _, x in pg.Resources.XObject.items():
            x.StructParents = 0
        pdf.save(src)
    out = tmp_path / "out.pdf"
    tag_document(src, out, prepare_analysis(src), title="T", language="en-US")
    with pikepdf.open(out) as pdf:
        findings, _ = check_integrity(pdf)
    assert not [f for f in findings if f.severity == "error"], [f.issue for f in findings]


def test_small_region_result_does_not_match_enclosing_figure(samples, tmp_path):
    """A decorative verdict for a tiny mark inside a figure must not turn the figure into an artifact."""
    src = samples / "partially_tagged.pdf"
    analysis = prepare_analysis(src)
    img = analysis.pages[0].images[0]
    x0, y0 = img.bbox[0] + 10, img.bbox[1] + 10
    out = tmp_path / "o.pdf"
    res = tag_document(src, out, analysis, title="T", language="en-US",
                       region_alts=[(0, (x0, y0, x0 + 3.6, y0 + 3.6), None, True)])
    assert res.existing_tags["artifacted"] == 0


def test_direct_object_elements_can_be_removed(samples, tmp_path):
    """Some producers write structure elements inline (no object number); removing one must work."""
    from pikepdf import Array, Dictionary, Name

    from app.verification.integrity import check_integrity

    src = tmp_path / "direct.pdf"
    with pikepdf.open(samples / "partially_tagged.pdf") as pdf:
        doc = pdf.Root.StructTreeRoot.K[0]
        pg = pdf.pages[0].obj
        # replace the indirect Figure (MCID 2) with an inline (direct) copy
        kids = list(doc.K)
        fig_i = next(i for i, k in enumerate(kids) if str(k.S) == "/Figure")
        kids[fig_i] = Dictionary(Type=Name.StructElem, S=Name.Figure, P=doc, Pg=pg, K=2)
        doc.K = Array(kids)
        pdf.save(src)
    analysis = prepare_analysis(src)
    img = analysis.pages[0].images[0]
    out = tmp_path / "o.pdf"
    res = tag_document(src, out, analysis, title="T", language="en-US", region_alts=[(0, img.bbox, None, True)])
    assert res.existing_tags["artifacted"] == 1
    with pikepdf.open(out) as pdf:
        assert not [f for f in check_integrity(pdf)[0] if f.severity == "error"]


def test_artifacted_figure_moved_by_list_repair_is_still_removed(samples, tmp_path):
    """A decorative figure directly inside an LI gets wrapped into an LBody first; removing it must
    still find it there (regression: the tag stayed behind pointing at artifact content)."""
    from pikepdf import Array, Dictionary, Name

    from app.verification.integrity import check_integrity

    src = tmp_path / "li_fig.pdf"
    with pikepdf.open(samples / "partially_tagged.pdf") as pdf:
        doc = pdf.Root.StructTreeRoot.K[0]
        kids = list(doc.K)
        fig = next(k for k in kids if str(k.S) == "/Figure")
        lst = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.L, P=doc, K=Array()))
        li = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.LI, P=lst, K=Array([fig])))
        lst.K.append(li)
        fig.P = li
        doc.K = Array([lst if k.objgen == fig.objgen else k for k in kids])
        pdf.save(src)
    analysis = prepare_analysis(src)
    img = analysis.pages[0].images[0]
    out = tmp_path / "o.pdf"
    tag_document(src, out, analysis, title="T", language="en-US", region_alts=[(0, img.bbox, None, True)])
    with pikepdf.open(out) as pdf:
        assert not [f for f in check_integrity(pdf)[0] if f.severity == "error"]
