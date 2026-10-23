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


def _list_split_by_note(tmp_path, second_label="2.", note_x=90):
    """A tagged page: item 1, a note under it, items 2 and 3, tagged (as Word does) as L, P, L."""
    from pikepdf import Array, Dictionary, Name

    pdf = pikepdf.new()
    lines = [(72, 700, "1. Is the work done inside the state?"),
             (note_x, 684, "*If not, use the other request form instead."),
             (72, 664, f"{second_label} Type of request: new or changed"),
             (72, 646, "3. Length of the arrangement")]
    ops = []
    for mcid, (x, y, text) in enumerate(lines):
        ops.append(f"/{'P' if mcid == 1 else 'LBody'} <</MCID {mcid}>> BDC BT /F1 11 Tf {x} {y} Td ({text}) Tj ET EMC")
    page = pikepdf.Page(Dictionary(
        Type=Name.Page, MediaBox=[0, 0, 612, 792],
        Resources=Dictionary(Font=Dictionary(F1=pdf.make_indirect(
            Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica)))),
        Contents=pdf.make_stream("\n".join(ops).encode())))
    pdf.pages.append(page)
    pg = pdf.pages[0].obj
    pg.StructParents = 0
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    doc = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Document, P=root, Pg=pg))
    mk = lambda s, parent, k=None: pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name(s), P=parent, Pg=pg,  # noqa: E731
                                                                K=k if k is not None else Array()))

    def item(lst, mcid):
        li = mk("/LI", lst)
        body = mk("/LBody", li, mcid)
        li.K = Array([body])
        return li, body

    l1, l2 = mk("/L", doc), mk("/L", doc)
    li1, b1 = item(l1, 0)
    l1.K = Array([li1])
    note = mk("/P", doc, 1)
    li2, b2 = item(l2, 2)
    li3, b3 = item(l2, 3)
    l2.K = Array([li2, li3])
    doc.K = Array([l1, note, l2])
    root.K = Array([doc])
    root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array([0, Array([b1, note, b2, b3])])))
    pdf.Root.StructTreeRoot = root
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    src = tmp_path / f"split_{second_label.strip('.')}_{note_x}.pdf"
    pdf.save(src)
    return src


def _top_roles(path):
    with pikepdf.open(path) as pdf:
        doc = pdf.Root.StructTreeRoot.K[0]
        return [str(k.S)[1:] for k in doc.K], doc


def test_a_list_split_by_a_note_is_joined(tmp_path):
    src = _list_split_by_note(tmp_path)
    out = tmp_path / "o.pdf"
    res = tag_document(src, out, prepare_analysis(src), title="T", language="en-US")
    reasons = " ".join(r for s in res.existing_tags["samples"] for r in s["reasons"])
    assert "joined" in reasons and "note under a list item" in reasons
    with pikepdf.open(out) as pdf:
        doc = pdf.Root.StructTreeRoot.K[0]
        lists = [k for k in doc.K if str(k.S) == "/L"]
        assert len(lists) == 1 and len(lists[0].K) == 3          # one list, three items
        first_body = lists[0].K[0].K[0]
        assert [str(k.S) for k in first_body.K if isinstance(k, pikepdf.Dictionary)] == ["/P"]  # the note, in item 1
        assert "/P" not in [str(k.S) for k in doc.K]
    # The tag manager summarizes the list in reading order
    from app.verification.tagmap import build_tagmap
    text = next(e for e in build_tagmap(out)["elements"] if e["role"] == "L")["text"]
    assert text.index("1. Is") < text.index("*If not") < text.index("2. Type") < text.index("3. Length"), text


def test_lists_that_dont_continue_are_left_alone(tmp_path):
    for label, x in (("1.", 90), ("2.", 72)):  # numbering starts again; the note isn't indented
        src = _list_split_by_note(tmp_path, second_label=label, note_x=x)
        out = tmp_path / f"o_{label}_{x}.pdf"
        tag_document(src, out, prepare_analysis(src), title="T", language="en-US")
        assert _top_roles(out)[0] == ["L", "P", "L"], (label, x)


def _table_tagged(tmp_path, shape):
    """A two-row table tagged as Word does: "rows_in_table" (THead, then the other row directly in the Table) or
    "header_only" (one row in THead, plus an empty TBody)."""
    from pikepdf import Array, Dictionary, Name

    pdf = pikepdf.new()
    cells = [(72, 700, "Name"), (300, 700, "Role"), (72, 680, "Pat"), (300, 680, "Lead")]
    ops = [f"/TD <</MCID {i}>> BDC BT /F1 11 Tf {x} {y} Td ({t}) Tj ET EMC" for i, (x, y, t) in enumerate(cells)]
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica))
    pdf.pages.append(pikepdf.Page(Dictionary(Type=Name.Page, MediaBox=[0, 0, 612, 792],
                                             Resources=Dictionary(Font=Dictionary(F1=font)),
                                             Contents=pdf.make_stream("\n".join(ops).encode()))))
    pg = pdf.pages[0].obj
    pg.StructParents = 0
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    doc = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Document, P=root, Pg=pg))
    mk = lambda s, parent, k=None: pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name(s), P=parent, Pg=pg,  # noqa: E731
                                                                K=k if k is not None else Array()))
    table = mk("/Table", doc)
    head = mk("/THead", table)
    leaves = []

    def row(parent, first, role):
        tr = mk("/TR", parent)
        tds = [mk(role, tr, first), mk(role, tr, first + 1)]
        leaves.extend(tds)
        tr.K = Array(tds)
        return tr

    head.K = Array([row(head, 0, "/TH")])
    if shape == "rows_in_table":
        table.K = Array([head, row(table, 2, "/TD")])
    else:  # header only: the body is empty and the second line is a plain paragraph
        p = mk("/P", doc, 2)
        p2 = mk("/P", doc, 3)
        leaves.extend([p, p2])
        table.K = Array([head, mk("/TBody", table)])
    doc.K = Array([table] + ([] if shape == "rows_in_table" else [leaves[-2], leaves[-1]]))
    root.K = Array([doc])
    root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array([0, Array(leaves)])))
    pdf.Root.StructTreeRoot = root
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    src = tmp_path / f"table_{shape}.pdf"
    pdf.save(src)
    return src


def _table_kids(path):
    with pikepdf.open(path) as pdf:
        doc = pdf.Root.StructTreeRoot.K[0]
        table = next(k for k in doc.K if str(k.S) == "/Table")
        return [(str(k.S)[1:], [str(r.S)[1:] for r in k.K] if str(k.S) in ("/THead", "/TBody") else None)
                for k in table.K]


def test_rows_after_a_thead_are_grouped_into_a_tbody(tmp_path):
    src = _table_tagged(tmp_path, "rows_in_table")
    out = tmp_path / "o.pdf"
    res = tag_document(src, out, prepare_analysis(src), title="T", language="en-US")
    assert _table_kids(out) == [("THead", ["TR"]), ("TBody", ["TR"])]
    assert "grouped into a TBody" in " ".join(r for s in res.existing_tags["samples"] for r in s["reasons"])


def test_a_header_only_table_gets_a_tbody(tmp_path):
    src = _table_tagged(tmp_path, "header_only")
    out = tmp_path / "o.pdf"
    res = tag_document(src, out, prepare_analysis(src), title="T", language="en-US")
    # The THead became the TBody, and then, a body with no head beside it (#438), its row stands in the table
    assert _table_kids(out) == [("TR", None)]  # its header cells stay TH
    with pikepdf.open(out) as pdf:
        table = next(k for k in pdf.Root.StructTreeRoot.K[0].K if str(k.S) == "/Table")
        assert [str(c.S) for c in table.K[0].K] == ["/TH", "/TH"]
    assert "THead became the TBody" in " ".join(r for s in res.existing_tags["samples"] for r in s["reasons"])


def _figure_alts(path):
    with pikepdf.open(path) as pdf:
        out = []

        def walk(n):
            k = n.get("/K")
            for c in (list(k) if isinstance(k, pikepdf.Array) else [k]):
                if isinstance(c, pikepdf.Dictionary) and "/S" in c:
                    if str(c.S) == "/Figure":
                        out.append(str(c.get("/Alt")) if "/Alt" in c else None)
                    walk(c)
        walk(pdf.Root.StructTreeRoot)
        return out


def test_office_automatic_alt_text_is_replaced_or_cleaned(samples, tmp_path):
    """Office marks the alt text it writes itself ("Description automatically generated"): ours replaces it when
    there is one; otherwise the note goes, as screen readers would read it out."""
    def build(pdf, pg, doc):
        fig = next(k for k in doc.K if str(k.S) == "/Figure")
        fig.Alt = pikepdf.String("A chart\n\nDescription automatically generated")

    src = _tagged_with(tmp_path, samples, lambda pdf, pg, doc: build(pdf, pg, doc))
    analysis = prepare_analysis(src)
    out = tmp_path / "cleaned.pdf"
    tag_document(src, out, analysis, title="T", language="en-US")
    assert _figure_alts(out) == ["A chart"]
    for img in analysis.pages[0].images:
        img.alt = "Bar chart of remediation progress by quarter"
    out2 = tmp_path / "replaced.pdf"
    res = tag_document(src, out2, analysis, title="T", language="en-US")
    assert _figure_alts(out2) == ["Bar chart of remediation progress by quarter"]
    assert "automatic alt text replaced" in " ".join(r for s in res.existing_tags["samples"] for r in s["reasons"])


def test_a_line_tagged_as_a_figure_becomes_an_artifact(tmp_path):
    """PowerPoint tags slide rules as figures: a line has no area, so it must be judged from its shape."""
    from pikepdf import Array, Dictionary, Name

    pdf = pikepdf.new()
    content = b"/P <</MCID 0>> BDC BT /F1 11 Tf 72 700 Td (Roadmap) Tj ET EMC\n" \
              b"/Figure <</MCID 1>> BDC 0 0 1 RG 1 w 30 670 m 900 670 l S EMC"
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica))
    pdf.pages.append(pikepdf.Page(Dictionary(Type=Name.Page, MediaBox=[0, 0, 960, 792],
                                             Resources=Dictionary(Font=Dictionary(F1=font)),
                                             Contents=pdf.make_stream(content))))
    pg = pdf.pages[0].obj
    pg.StructParents = 0
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    doc = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Document, P=root, Pg=pg))
    p = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.P, P=doc, Pg=pg, K=0))
    fig = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Figure, P=doc, Pg=pg, K=1))
    doc.K = Array([p, fig])
    root.K = Array([doc])
    root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array([0, Array([p, fig])])))
    pdf.Root.StructTreeRoot = root
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    src = tmp_path / "rule.pdf"
    pdf.save(src)
    out = tmp_path / "o.pdf"
    res = tag_document(src, out, prepare_analysis(src), title="T", language="en-US")
    assert res.existing_tags["artifacted"] == 1 and _figure_alts(out) == []


def _badges(tmp_path):
    """A slide row of number badges as PowerPoint writes them: each badge is a small picture marked as its own
    content but left out of the tag tree, with its number (tagged) printed on it; plus a photo whose corner the
    last badge crosses."""
    import pymupdf as fitz
    from pikepdf import Array, Dictionary, Name

    def png(w, h, rgb):
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, w, h), False)
        pix.set_rect(pix.irect, rgb)
        return pix.tobytes("png")

    base = fitz.open()
    page = base.new_page(width=960, height=792)
    page.insert_image(fitz.Rect(0, 0, 10, 10), stream=png(40, 40, (90, 30, 160)))      # the disc
    page.insert_image(fitz.Rect(20, 0, 30, 10), stream=png(80, 60, (200, 180, 150)))   # the photo
    page.insert_text((0, 20), "x", fontname="helv")
    disc, photo_im = [i[7] for i in page.get_images(full=True)]
    ops = ["/P <</MCID 0>> BDC BT /helv 20 Tf 72 72 Td (Student First) Tj ET EMC",
           f"/Figure <</MCID 1>> BDC q 200 0 0 150 560 100 cm /{photo_im} Do Q EMC"]
    for i, x in enumerate((100, 220, 340, 460, 580)):  # the last badge crosses the photo's corner
        ops.append(f"/P <</MCID {2 + 2 * i}>> BDC q 64 0 0 64 {x} 220 cm /{disc} Do Q EMC")  # never referenced
        ops.append(f"/Span <</MCID {3 + 2 * i}>> BDC BT /helv 14 Tf {x + 27} 247 Td ({i + 1}) Tj ET EMC")
    first, *rest = page.get_contents()
    base.update_stream(first, "\n".join(ops).encode())
    for xref in rest:  # the placeholder draws of the inserts
        base.update_stream(xref, b"")
    raw = tmp_path / "raw.pdf"
    base.save(raw)

    pdf = pikepdf.open(raw)
    pg = pdf.pages[0].obj
    pg.StructParents = 0
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    doc = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Document, P=root, Pg=pg))
    mk = lambda s, k, **kw: pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name(s), P=doc, Pg=pg, K=k, **kw))  # noqa: E731
    title = mk("/P", 0)
    photo = mk("/Figure", 1, Alt=pikepdf.String("A student holding a laptop"))
    numbers = [mk("/P", 3 + 2 * i) for i in range(5)]
    doc.K = Array([title, photo, *numbers])
    root.K = Array([doc])
    parents = [title, photo] + [x for n in numbers for x in (None, n)]
    root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array([0, Array(parents)])))
    pdf.Root.StructTreeRoot = root
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    src = tmp_path / "badges.pdf"
    pdf.save(src)
    return src


def test_number_badges_are_decorative_backgrounds(tmp_path):
    """Each badge becomes an artifact; none becomes a Figure, an empty paragraph, or part of the photo's Figure."""
    from app.verification.integrity import check_integrity
    from app.verification.tagmap import build_tagmap

    src = _badges(tmp_path)
    analysis = prepare_analysis(src)
    discs = [i for i in analysis.pages[0].images if (i.bbox[2] - i.bbox[0]) < 100]
    assert len(discs) == 5 and all(i.decorative for i in discs)   # the number on each says what it is
    out = tmp_path / "o.pdf"
    res = tag_document(src, out, analysis, title="T", language="en-US")
    assert res.orphans_artifacted == 5
    els = build_tagmap(out)["elements"]
    figures = [e for e in els if e["role"] == "Figure"]
    assert len(figures) == 1 and figures[0]["boxes"][0]["bbox"][0] > 500   # just the photo
    assert not [e for e in els if e["role"] == "P" and not e.get("text")]  # no empty paragraph
    assert sorted(e["text"] for e in els if e["role"] == "P" and e.get("text", "").isdigit()) == list("12345")
    with pikepdf.open(out) as pdf:
        assert not [f for f in check_integrity(pdf)[0] if f.severity == "error"]


def test_control_characters_are_removed_from_kept_tag_text(samples, tmp_path):
    """Word's PDFMaker can end alt text with the NUL that ends a C string: it goes, and the rest stays. Alt text that
    was nothing but control characters counts as none."""
    def build(pdf, pg, doc):
        fig = next(k for k in doc.K if str(k.S) == "/Figure")
        fig.Alt = pikepdf.String("---\x00")
        fig.ActualText = pikepdf.String("Line one\nline\x07 two\x00")

    src = _tagged_with(tmp_path, samples, build)
    out = tmp_path / "o.pdf"
    res = tag_document(src, out, prepare_analysis(src), title="T", language="en-US")
    assert _figure_alts(out) == ["---"]
    with pikepdf.open(out) as pdf:
        figs = [o for o in pdf.objects if isinstance(o, pikepdf.Dictionary) and str(o.get("/S")) == "/Figure"]
        assert [str(f.ActualText) for f in figs if "/ActualText" in f] == ["Line one\nline two"]
        assert not any(b"\x00" in bytes(f[k]) for f in figs for k in ("/Alt", "/ActualText") if k in f)
    reasons = " ".join(r for s in res.existing_tags["samples"] for r in s["reasons"])
    assert "control characters removed from the alt text" in reasons and "from the read-aloud text" in reasons

    def only_nul(pdf, pg, doc):
        next(k for k in doc.K if str(k.S) == "/Figure").Alt = pikepdf.String("\x00")

    src = _tagged_with(tmp_path, samples, only_nul)
    out = tmp_path / "o2.pdf"
    res = tag_document(src, out, prepare_analysis(src), title="T", language="en-US")
    assert _figure_alts(out) in ([], [None])  # undescribed: no figure, or one without alt text
    assert "missing Alt" in " ".join(r for s in res.existing_tags["samples"] for r in s["reasons"])


def _numbered_heading(tmp_path):
    """InDesign's numbered box anchored in its heading (#486): Header (-> H2) > Story (-> Sect) > Number (-> H2)."""
    from pikepdf import Array, Dictionary, Name

    pdf = pikepdf.new()
    lines = [(72, 720, "Account form"), (72, 700, "4."), (90, 700, "Transfer the account to a new owner"),
             (72, 680, "Fill in the new owner's name and address below.")]
    ops = [f"/P <</MCID {m}>> BDC BT /F1 11 Tf {x} {y} Td ({t}) Tj ET EMC" for m, (x, y, t) in enumerate(lines)]
    page = pikepdf.Page(Dictionary(
        Type=Name.Page, MediaBox=[0, 0, 612, 792],
        Resources=Dictionary(Font=Dictionary(F1=pdf.make_indirect(
            Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica)))),
        Contents=pdf.make_stream("\n".join(ops).encode())))
    pdf.pages.append(page)
    pg = pdf.pages[0].obj
    pg.StructParents = 0
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot, RoleMap=Dictionary(
        Story=Name.Sect, App_Header=Name.H2, Box_Number=Name.H2)))
    mk = lambda s, parent, k=None: pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name(s), P=parent, Pg=pg,  # noqa: E731
                                                                K=k if k is not None else Array()))
    doc = mk("/Document", root)
    title = mk("/H1", doc, 0)
    head = mk("/App_Header", doc)
    story = mk("/Story", head)
    number = mk("/Box_Number", story, 1)
    story.K = Array([number])
    head.K = Array([story, 2])
    body = mk("/P", doc, 3)
    doc.K = Array([title, head, body])
    root.K = Array([doc])
    root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array([0, Array([title, number, head, body])])))
    pdf.Root.StructTreeRoot = root
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    src = tmp_path / "numbered.pdf"
    pdf.save(src)
    return src


def test_a_section_inside_a_heading_becomes_part_of_it(tmp_path):
    src = _numbered_heading(tmp_path)
    out = tmp_path / "o.pdf"
    res = tag_document(src, out, prepare_analysis(src), title="T", language="en-US")
    reasons = " ".join(r for s in res.existing_tags["samples"] for r in s["reasons"])
    assert "made part of it" in reasons
    with pikepdf.open(out) as pdf:
        rm = {str(k): str(v) for k, v in dict(pdf.Root.StructTreeRoot.get("/RoleMap") or {}).items()}

        def walk(e):
            for k in (e.K if isinstance(e.get("/K"), pikepdf.Array) else [e.get("/K")]):
                if isinstance(k, pikepdf.Dictionary) and k.get("/Type") not in (pikepdf.Name.MCR, pikepdf.Name.OBJR):
                    yield k
                    yield from walk(k)
        roles = [rm.get(str(e.S), str(e.S)) for e in walk(pdf.Root.StructTreeRoot)]
        assert "/Sect" not in roles and roles.count("/H2") == 1
        heading = next(e for e in walk(pdf.Root.StructTreeRoot) if rm.get(str(e.S), str(e.S)) == "/H2")
        assert list(heading.K) == [1, 2]  # the number, then the heading's words: one heading
    from app.verification.tagmap import build_tagmap
    head = next(e for e in build_tagmap(out)["elements"] if e["role"] == "H2")
    assert head["text"].startswith("4.") and "Transfer" in head["text"]


def _word_picture(tmp_path, alt, lines_only=False):
    from pikepdf import Array, Dictionary, Name, String

    pdf = pikepdf.new()
    ops = ["/P <</MCID 0>> BDC BT /F1 11 Tf 72 700 Td (Which compound is a phenol?) Tj ET EMC",
           "/Figure <</MCID 1>> BDC " + ("0 G 2 w 72 600 m 152 660 l S 152 600 m 72 660 l S" if lines_only
                                         else "q 80 0 0 60 72 600 cm /Im1 Do Q") + " EMC"]
    image = pdf.make_stream(bytes(range(256)) * 3 * 16, Type=Name.XObject, Subtype=Name.Image, Width=64, Height=64,
                            ColorSpace=Name.DeviceRGB, BitsPerComponent=8)
    page = pikepdf.Page(Dictionary(
        Type=Name.Page, MediaBox=[0, 0, 612, 792],
        Resources=Dictionary(Font=Dictionary(F1=pdf.make_indirect(
            Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica))), XObject=Dictionary(Im1=image)),
        Contents=pdf.make_stream("\n".join(ops).encode())))
    pdf.pages.append(page)
    pg = pdf.pages[0].obj
    pg.StructParents = 0
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot, RoleMap=Dictionary(InlineShape=Name.Sect)))
    doc = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Document, P=root, Pg=pg))
    para = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.P, P=doc, Pg=pg))
    shape = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name("/InlineShape"), P=para, Pg=pg, K=1,
                                         Alt=String(alt)))
    para.K = Array([0, shape])
    doc.K = Array([para])
    root.K = Array([doc])
    root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array([0, Array([para, shape])])))
    pdf.Root.StructTreeRoot = root
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    src = tmp_path / f"shape{len(alt)}{lines_only}.pdf"
    pdf.save(src)
    out = tmp_path / f"o{len(alt)}{lines_only}.pdf"
    tag_document(src, out, prepare_analysis(src), title="T", language="en-US")
    with pikepdf.open(out) as pdf:
        para = pdf.Root.StructTreeRoot.K[0].K[0]
        (figure,) = [k for k in para.K if isinstance(k, pikepdf.Dictionary)]
        return str(figure.S), str(figure.get("/Alt") or "")


def test_a_word_picture_mapped_to_sect_becomes_a_figure(tmp_path):
    """Word's InlineShape, mapped to Sect, inside a paragraph: a Figure, never dissolved into the paragraph (#486).
    Its description is kept; an empty one is left for the AI's description, as any figure's is (none here)."""
    assert _word_picture(tmp_path, "Six benzene rings, numbered I to VI") == ("/Figure",
                                                                               "Six benzene rings, numbered I to VI")
    assert _word_picture(tmp_path, "")[0] == "/Figure"
    # Drawn in lines only, but described: a figure, not decoration
    assert _word_picture(tmp_path, "A crossed-out box", lines_only=True) == ("/Figure", "A crossed-out box")


def test_blank_lines_kept_as_paragraphs_become_artifacts(tmp_path):
    """#490: Word's blank lines are paragraphs holding a space, and a picture alone in its paragraph has a space beside
    it. Those runs become artifacts and a blank paragraph goes; spaces between a paragraph's words stay."""
    from pikepdf import Array, Dictionary, Name

    pdf = pikepdf.new()
    image = pdf.make_stream(bytes(range(256)) * 3 * 16, Type=Name.XObject, Subtype=Name.Image, Width=64, Height=64,
                            ColorSpace=Name.DeviceRGB, BitsPerComponent=8)
    t = "BT /F1 11 Tf {x} {y} Td ({s}) Tj ET"
    ops = [f"/P <</MCID 0>> BDC {t.format(x=72, y=720, s='Which compound is a phenol?')} EMC",
           f"/P <</MCID 1>> BDC {t.format(x=72, y=700, s=' ')} EMC",                       # a blank line
           "/Figure <</MCID 2>> BDC q 80 0 0 60 72 620 cm /Im1 Do Q EMC",
           f"/P <</MCID 3>> BDC {t.format(x=152, y=620, s=' ')} EMC",                      # the space by the picture
           f"/P <</MCID 4>> BDC {t.format(x=72, y=600, s='Answer')} EMC",
           f"/P <</MCID 5>> BDC {t.format(x=110, y=600, s=' ')} EMC",                      # between two words
           f"/P <</MCID 6>> BDC {t.format(x=115, y=600, s='below')} EMC"]
    page = pikepdf.Page(Dictionary(
        Type=Name.Page, MediaBox=[0, 0, 612, 792],
        Resources=Dictionary(Font=Dictionary(F1=pdf.make_indirect(
            Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica))), XObject=Dictionary(Im1=image)),
        Contents=pdf.make_stream("\n".join(ops).encode())))
    pdf.pages.append(page)
    pg = pdf.pages[0].obj
    pg.StructParents = 0
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    mk = lambda s, parent, k=None, **kw: pdf.make_indirect(Dictionary(  # noqa: E731
        Type=Name.StructElem, S=Name(s), P=parent, Pg=pg, K=k if k is not None else Array(), **kw))
    doc = mk("/Document", root)
    question, blank = mk("/P", doc, 0), mk("/P", doc, 1)
    holder = mk("/P", doc)
    figure = mk("/Figure", holder, 2, Alt=pikepdf.String("A benzene ring with OH"))
    holder.K = Array([figure, 3])
    words = mk("/P", doc, Array([4, 5, 6]))
    doc.K = Array([question, blank, holder, words])
    root.K = Array([doc])
    root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array([0, Array([question, blank, figure, holder, words,
                                                                           words, words])])))
    pdf.Root.StructTreeRoot = root
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    src = tmp_path / "blank.pdf"
    pdf.save(src)
    out = tmp_path / "o.pdf"
    res = tag_document(src, out, prepare_analysis(src), title="T", language="en-US")
    assert res.blank_runs_artifacted == 2
    with pikepdf.open(out) as pdf:
        doc = pdf.Root.StructTreeRoot.K[0]
        kids = [k for k in doc.K if isinstance(k, pikepdf.Dictionary)]
        assert [str(k.S) for k in kids] == ["/P", "/P", "/P"]  # the blank paragraph is gone
        holder = kids[1]
        assert [str(k.S) for k in holder.K if isinstance(k, pikepdf.Dictionary)] == ["/Figure"]
        assert not [k for k in holder.K if not isinstance(k, pikepdf.Dictionary)]  # its space went
        assert len([k for k in kids[2].K]) == 3  # the space between the words stays
    # The two spaces are artifacts now, not left untagged: every run still marked names a tag
    with pikepdf.open(out) as pdf:
        tagged, artifacts = set(), 0
        for operands, op in pikepdf.parse_content_stream(pdf.pages[0]):
            if str(op) == "BDC" and isinstance(operands[1], pikepdf.Dictionary) and "/MCID" in operands[1]:
                tagged.add(int(operands[1].MCID))
            if str(op) in ("BDC", "BMC") and str(operands[0]) == "/Artifact":
                artifacts += 1
        assert artifacts >= 2 and not tagged & {1, 3}



def test_a_space_in_a_span_straight_in_a_text_box_becomes_an_artifact(tmp_path):
    """#526: a text box (Textbox, mapped to Sect) holding a paragraph and a Span of one space. Folded into the box, the
    space would be content in a Sect, which PAC flags; it becomes an artifact and the Span goes. A Span of one space
    between two words in a paragraph stays (empty_spans folds it into the paragraph)."""
    from pikepdf import Array, Dictionary, Name

    pdf = pikepdf.new()
    t = "BT /F1 11 Tf {x} {y} Td ({s}) Tj ET"
    ops = [f"/P <</MCID 0>> BDC {t.format(x=72, y=720, s='Hypoglycemia')} EMC",
           f"/Span <</MCID 1>> BDC q 70 690 200 40 re W n {t.format(x=72, y=700, s=' ')} Q EMC",  # the stray space
           f"/Span <</MCID 2>> BDC {t.format(x=72, y=680, s='Level')} EMC",
           f"/Span <</MCID 3>> BDC {t.format(x=100, y=680, s=' ')} EMC",                         # between two words
           f"/Span <</MCID 4>> BDC {t.format(x=105, y=680, s='one')} EMC"]
    page = pikepdf.Page(Dictionary(
        Type=Name.Page, MediaBox=[0, 0, 612, 792],
        Resources=Dictionary(Font=Dictionary(F1=pdf.make_indirect(
            Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica)))),
        Contents=pdf.make_stream("\n".join(ops).encode())))
    pdf.pages.append(page)
    pg = pdf.pages[0].obj
    pg.StructParents = 0
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot, RoleMap=Dictionary(Textbox=Name.Sect)))
    mk = lambda s, parent, k=None: pdf.make_indirect(Dictionary(  # noqa: E731
        Type=Name.StructElem, S=Name(s), P=parent, Pg=pg, K=k if k is not None else Array()))
    doc = mk("/Document", root)
    box = mk("/Textbox", doc)
    para, stray = mk("/P", box, 0), mk("/Span", box, 1)
    words = mk("/P", doc)
    spans = [mk("/Span", words, n) for n in (2, 3, 4)]
    words.K = Array(spans)
    box.K = Array([para, stray])
    doc.K = Array([box, words])
    root.K = Array([doc])
    root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array([0, Array([para, stray] + spans)])))
    pdf.Root.StructTreeRoot = root
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    src = tmp_path / "box.pdf"
    pdf.save(src)
    out = tmp_path / "o.pdf"
    res = tag_document(src, out, prepare_analysis(src), title="T", language="en-US")
    assert res.blank_runs_artifacted == 1
    with pikepdf.open(out) as pdf:
        doc = pdf.Root.StructTreeRoot.K[0]
        box = doc.K[0]
        assert [str(k.S) for k in box.K] == ["/P"]  # the Span and its space have left the box
        assert len(doc.K[1].K) == 3  # the space between the words stays, in its paragraph
        tagged, artifacts = set(), 0
        for operands, op in pikepdf.parse_content_stream(pdf.pages[0]):
            if str(op) == "BDC" and isinstance(operands[1], pikepdf.Dictionary) and "/MCID" in operands[1]:
                tagged.add(int(operands[1].MCID))
            if str(op) in ("BDC", "BMC") and str(operands[0]) == "/Artifact":
                artifacts += 1
        assert artifacts >= 1 and 1 not in tagged

def _linked_paragraph(tmp_path, runs, rects=None, quads=None, name="link"):
    """A paragraph of runs ((x, y, text) each, one MCID per run), the last in a Span of its own, with a link annotation
    attached straight to the paragraph, as some producers do: the kept-tags repair wraps the annotation alone in a
    Link. The link covers `rects` (PDF space) or its QuadPoints."""
    from pikepdf import Array, Dictionary, Name

    pdf = pikepdf.new()
    ops = [f"/P <</MCID {n}>> BDC BT /F1 11 Tf {x} {y} Td ({s}) Tj ET EMC" for n, (x, y, s) in enumerate(runs)]
    page = pikepdf.Page(Dictionary(
        Type=Name.Page, MediaBox=[0, 0, 612, 792],
        Resources=Dictionary(Font=Dictionary(F1=pdf.make_indirect(
            Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica)))),
        Contents=pdf.make_stream("\n".join(ops).encode())))
    pdf.pages.append(page)
    pg = pdf.pages[0].obj
    pg.StructParents = 0
    annot = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link, Rect=Array(rects), StructParent=1,
                                         A=Dictionary(S=Name.URI, URI=pikepdf.String("https://example.org"))))
    if quads:
        annot.QuadPoints = Array(quads)
    pg.Annots = Array([annot])
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    doc = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Document, P=root, Pg=pg))
    para = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.P, P=doc, Pg=pg))
    span = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Span, P=para, Pg=pg, K=len(runs) - 1))
    para.K = Array(list(range(len(runs) - 1)) + [span, Dictionary(Type=Name.OBJR, Obj=annot, Pg=pg)])
    doc.K = Array([para])
    root.K = Array([doc])
    root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array([0, Array([para] * (len(runs) - 1) + [span]), 1,
                                                               para])))
    pdf.Root.StructTreeRoot = root
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    src = tmp_path / f"{name}.pdf"
    pdf.save(src)
    out = tmp_path / f"{name}_out.pdf"
    res = tag_document(src, out, prepare_analysis(src), title="T", language="en-US")
    with pikepdf.open(out) as pdf:
        para = pdf.Root.StructTreeRoot.K[0].K[0]

        def shape(k):
            if isinstance(k, pikepdf.Dictionary) and k.get("/Type") == pikepdf.Name.OBJR:
                return "annot"
            if isinstance(k, pikepdf.Dictionary) and "/S" in k:
                return (str(k.S), [shape(x) for x in (k.K if isinstance(k.K, pikepdf.Array) else [k.K])])
            return int(k) if not isinstance(k, pikepdf.Dictionary) else int(k.MCID)
        return res.links_given_words, [shape(k) for k in para.K]


def test_a_link_tag_takes_the_words_inside_the_link(tmp_path):
    """#489: the link's words are their own run: they move into the Link, which takes their place."""
    runs = [(72, 700, "See the "), (112, 700, "course page"), (172, 700, " for details."), (72, 686, "Thanks.")]
    assert _linked_paragraph(tmp_path, runs, rects=[110, 696, 172, 711], name="own") == (
        1, [0, ("/Link", [1, "annot"]), 2, ("/Span", [3])])


def test_a_run_the_link_only_partly_covers_stays(tmp_path):
    """A whole line tagged as one run, the link over a few of its words: a run can't be split, so it stays."""
    runs = [(72, 700, "See the course page for details."), (72, 686, "Thanks.")]
    assert _linked_paragraph(tmp_path, runs, rects=[112, 696, 172, 711], name="whole") == (
        0, [0, ("/Span", [1]), ("/Link", ["annot"])])


def test_a_link_across_two_lines_takes_both_parts(tmp_path):
    """A link that wraps: its QuadPoints give a box on each line, and the run on each goes into the Link."""
    runs = [(72, 700, "Read the "), (120, 700, "full"), (72, 686, "guidelines"), (125, 686, " first."),
            (72, 672, "Thanks.")]
    quads = [118, 711, 145, 711, 118, 696, 145, 696, 70, 697, 125, 697, 70, 682, 125, 682]
    assert _linked_paragraph(tmp_path, runs, rects=[70, 682, 145, 711], quads=quads, name="wrap") == (
        1, [0, ("/Link", [1, 2, "annot"]), 3, ("/Span", [4])])


def _links_apart(tmp_path, cells, rects, name):
    """A table of one row, a cell per entry of `cells` (runs: (x, y, text) each, one MCID per run), and after it a
    paragraph holding nothing but a Link tag per entry of `rects` (PDF space), each holding only its annotation, as
    Word writes a table's links. Returns (links given words, the Document's kids as (tag, kids))."""
    from pikepdf import Array, Dictionary, Name

    pdf = pikepdf.new()
    runs = [r for cell in cells for r in cell]
    ops = [f"/P <</MCID {n}>> BDC BT /F1 11 Tf {x} {y} Td ({s}) Tj ET EMC" for n, (x, y, s) in enumerate(runs)]
    page = pikepdf.Page(Dictionary(
        Type=Name.Page, MediaBox=[0, 0, 612, 792],
        Resources=Dictionary(Font=Dictionary(F1=pdf.make_indirect(
            Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica)))),
        Contents=pdf.make_stream("\n".join(ops).encode())))
    pdf.pages.append(page)
    pg = pdf.pages[0].obj
    pg.StructParents = 0
    annots = [pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link, Rect=Array(r), StructParent=n + 1,
                                           A=Dictionary(S=Name.URI, URI=pikepdf.String(f"https://example.org/{n}"))))
              for n, r in enumerate(rects)]
    pg.Annots = Array(annots)
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))

    def mk(s, parent, k=None):
        e = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name(s), P=parent, Pg=pg))
        if k is not None:
            e.K = k
        return e

    doc = mk("/Document", root)
    table = mk("/Table", doc)
    tr = mk("/TR", table)
    tds, n, owners = [], 0, []
    for cell in cells:
        tds.append(mk("/TD", tr, Array(list(range(n, n + len(cell))))))
        owners += [tds[-1]] * len(cell)
        n += len(cell)
    tr.K, table.K = Array(tds), Array([tr])
    para = mk("/P", doc)
    links = [mk("/Link", para, Array([Dictionary(Type=Name.OBJR, Obj=a, Pg=pg)])) for a in annots]
    para.K = Array(links)
    doc.K = Array([table, para])
    root.K = Array([doc])
    nums = [0, Array(owners)]
    for i, link in enumerate(links):
        nums += [i + 1, link]
    root.ParentTree = pdf.make_indirect(Dictionary(Nums=Array(nums)))
    pdf.Root.StructTreeRoot = root
    pdf.Root.MarkInfo = Dictionary(Marked=True)
    src = tmp_path / f"{name}.pdf"
    pdf.save(src)
    out = tmp_path / f"{name}_out.pdf"
    res = tag_document(src, out, prepare_analysis(src), title="T", language="en-US")
    with pikepdf.open(out) as pdf:
        def shape(k):
            if isinstance(k, pikepdf.Dictionary) and k.get("/Type") == pikepdf.Name.OBJR:
                return "annot"
            if isinstance(k, pikepdf.Dictionary) and "/S" in k:
                return (str(k.S), [shape(x) for x in (k.K if isinstance(k.K, pikepdf.Array) else [k.K])])
            return int(k) if not isinstance(k, pikepdf.Dictionary) else int(k.MCID)
        doc = pdf.Root.StructTreeRoot.K[0]
        return res.links_given_words, [shape(k) for k in doc.K]


def test_a_tables_links_move_into_the_cells_holding_their_words(tmp_path):
    """#489: Word's table links, in a paragraph of their own, go into the cell with their words; the paragraph goes."""
    cells = [[(72, 700, "Overview"), (140, 700, ", page "), (180, 700, "4")],
             [(300, 700, "Modes"), (360, 700, " (see below)")]]
    assert _links_apart(tmp_path, cells, [[70, 696, 135, 711], [178, 696, 190, 711], [298, 696, 340, 711]],
                        "table") == (3, [
                            ("/Table", [("/TR", [("/TD", [("/Link", [0, "annot"]), 1, ("/Link", [2, "annot"])]),
                                                 ("/TD", [("/Link", [3, "annot"]), 4])])])])


def test_a_link_whose_box_covers_words_in_two_tags_stays(tmp_path):
    """Words inside the link's box in two cells: whose link it is isn't certain, so it stays as it was."""
    cells = [[(72, 700, "Left")], [(110, 700, "Right")]]
    assert _links_apart(tmp_path, cells, [[70, 696, 150, 711]], "two") == (0, [
        ("/Table", [("/TR", [("/TD", [0]), ("/TD", [1])])]), ("/P", [("/Link", ["annot"])])])
