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
    assert _table_kids(out) == [("TBody", ["TR"])]  # its header cells stay TH
    with pikepdf.open(out) as pdf:
        table = next(k for k in pdf.Root.StructTreeRoot.K[0].K if str(k.S) == "/Table")
        assert [str(c.S) for c in table.K[0].K[0].K] == ["/TH", "/TH"]
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
