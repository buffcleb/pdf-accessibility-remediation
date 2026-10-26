"""#445: a finished PDF/UA-1 file made PDF/UA-2 (app/tagging/pdfua2.py), and the run's PDF/UA choice: "2" delivers
the files as PDF/UA-2, "both" adds a PDF/UA-2 copy of the main file."""
import asyncio

import pikepdf
from pikepdf import Array, Dictionary, Name, String

from app.pipeline.orchestrator import JobOptions, run_remediation, prepare_analysis
from app.storage import JobStore
from app.tagging.pdfua2 import PDF2_NS, upgrade
from app.tagging.tagger import tag_document
from tests.engine.test_endnotes import _book


def _tagged(tmp_path):
    """A book with endnotes, tagged as the engine tags it (PDF/UA-1): Notes, References naming them, headings."""
    src = _book(tmp_path)
    out = tmp_path / "tagged.pdf"
    tag_document(src, out, prepare_analysis(src), title="Book", language="en-US")
    return out


def _elems(pdf):
    out, stack = [], [pdf.Root.StructTreeRoot]
    while stack:
        e = stack.pop()
        k = e.get("/K")
        for c in (list(k) if isinstance(k, Array) else [k]):
            if isinstance(c, Dictionary) and "/S" in c and c.get("/Type") not in (Name.MCR, Name.OBJR):
                out.append(c)
                stack.append(c)
    return out


def test_a_pdfua1_file_becomes_pdf_2_labelled_pdfua2_with_notes_as_fenote(tmp_path):
    out = tmp_path / "ua2.pdf"
    changes = upgrade(_tagged(tmp_path), out)
    assert changes[0].startswith("saved as PDF 2.0") and any("FENote" in c for c in changes)
    with pikepdf.open(out) as pdf:
        assert str(pdf.pdf_version) == "2.0"
        with pdf.open_metadata() as meta:
            assert (meta["pdfuaid:part"], meta["pdfuaid:rev"]) == ("2", "2024")
        (doc,) = list(pdf.Root.StructTreeRoot.K) if isinstance(pdf.Root.StructTreeRoot.K, Array) \
            else [pdf.Root.StructTreeRoot.K]
        assert str(doc.NS.NS) == PDF2_NS
        elems = _elems(pdf)
        notes = [e for e in elems if e.get("/S") == Name.FENote]
        assert len(notes) == 3 and not [e for e in elems if e.get("/S") == Name.Note]
        assert all(str(n.NS.NS) == PDF2_NS and bytes(n.ID).startswith(b"note-") for n in notes)
        # Both ways: each reference names its note, and each note names what cites it
        refs = [e for e in elems if e.get("/S") == Name.Reference]
        for r in refs:
            note = r.Ref[0]
            assert r.objgen in {c.objgen for c in note.Ref}


def test_lists_destinations_fields_files_and_private_use_text(tmp_path):
    src = _tagged(tmp_path)
    with pikepdf.open(src, allow_overwriting_input=True) as pdf:
        root = pdf.Root.StructTreeRoot
        doc = root.K[0] if isinstance(root.K, Array) else root.K
        heading = next(e for e in _elems(pdf) if str(e.get("/S", "")).startswith("/H"))
        page = heading.Pg
        # A kept list that says None though its items have labels; its label read from ActualText
        lbl = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Lbl, ActualText=String("1.")))
        li = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.LI, K=Array([lbl])))
        lst = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.L, K=Array([li]),
                                           A=Dictionary(O=Name.List, ListNumbering=Name("/None"))))
        doc.K.append(lst)
        # A paragraph whose read-aloud text has a private use character in it
        para = next(e for e in _elems(pdf) if e.get("/S") == Name.P)
        para.ActualText = String("Ag+  Ag")
        # A link and the file's opening action going to that page; a form field with only a name; an attachment
        link = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link, Rect=Array([0, 0, 10, 10]),
                                            Dest=Array([page, Name.XYZ, 0, 700, 0])))
        widget = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Widget, Rect=Array([0, 0, 10, 10]),
                                              FT=Name.Tx, T=String("first_name")))
        pdf.pages[0].obj.Annots = Array([link, widget])
        pdf.Root.OpenAction = Array([page, Name.Fit])
        spec = pdf.make_indirect(Dictionary(Type=Name.Filespec, F=String("data.csv"), UF=String("data.csv")))
        pdf.Root.Names = Dictionary(EmbeddedFiles=Dictionary(Names=Array([String("data.csv"), spec])))
        pdf.save(src)
    out = tmp_path / "ua2.pdf"
    upgrade(src, out)
    with pikepdf.open(out) as pdf:
        elems = _elems(pdf)
        lst = next(e for e in elems if e.get("/S") == Name.L)
        assert lst.A.ListNumbering == Name.Decimal
        assert any(str(e.get("/ActualText", "")) == "Ag+ Ag" for e in elems)
        link, widget = list(pdf.pages[0].obj.Annots)
        assert "/Dest" not in link and link.A.S == Name.GoTo and list(link.A.SD)[1:] == list(link.A.D)[1:]
        assert str(link.A.SD[0].get("/S", "")).startswith("/H")       # the heading on that page, the same view
        assert pdf.Root.OpenAction.S == Name.GoTo and "/SD" in pdf.Root.OpenAction
        assert str(widget.Contents) == "first_name"
        files = pikepdf.NameTree(pdf.Root.Names.EmbeddedFiles)
        assert str(files["data.csv"].Desc) == "data.csv"


def test_a_word_style_toc_item_holds_a_paragraph_and_names_its_target(tmp_path):
    """#413: Word writes a TOCI's link and text straight in it, which PDF 2.0 doesn't allow (Table 5), and no Ref
    (8.2.5.8). The PDF/UA-2 step puts them in a P, the parent tree following, and names the link's target."""
    from app.tagging.tree import number_tree_get
    from app.verification.integrity import check_integrity

    src = _tagged(tmp_path)
    with pikepdf.open(src, allow_overwriting_input=True) as pdf:
        root = pdf.Root.StructTreeRoot
        doc = root.K[0] if isinstance(root.K, Array) else root.K
        heading = next(e for e in _elems(pdf) if str(e.get("/S", "")).startswith("/H"))
        # A paragraph of the first page made a TOC entry, as Word tags one: its text, and a link beside it
        toci = next(e for e in _elems(pdf) if e.get("/S") == Name.P and e.get("/Pg") == pdf.pages[0].obj)
        toci.S = Name.TOCI
        page = pdf.pages[0].obj
        key = max(int(k) for k in list(root.ParentTree.Nums)[0::2]) + 1
        annot = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link, Rect=Array([0, 0, 10, 10]),
                                             Dest=Array([heading.Pg, Name.Fit]), StructParent=key))
        page.Annots = Array([annot])
        link = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Link, P=toci,
                                            K=Array([Dictionary(Type=Name.OBJR, Obj=annot, Pg=page)])))
        root.ParentTree.Nums.append(key)
        root.ParentTree.Nums.append(link)
        toci.K = Array([link, *(list(toci.K) if isinstance(toci.K, Array) else [toci.K])])
        content = [k for k in toci.K if not (isinstance(k, Dictionary) and "/S" in k)]
        toc =pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.TOC, P=doc, K=Array([toci])))
        parent = toci.P
        parent.K = Array([toc if (isinstance(k, Dictionary) and k.is_indirect and k.objgen == toci.objgen) else k
                          for k in (list(parent.K) if isinstance(parent.K, Array) else [parent.K])])
        toc.P, toci.P = parent, toc
        assert content
        pdf.save(src)
    out = tmp_path / "ua2.pdf"
    changes = upgrade(src, out)
    assert any("table of contents item(s) hold" in c for c in changes)
    with pikepdf.open(out) as pdf:
        toci = next(e for e in _elems(pdf) if e.get("/S") == Name.TOCI)
        # Only what a TOCI may hold: the text and link in Ps, the note references (allowed) left between them
        assert {str(k.S) for k in toci.K} <= {"/P", "/Reference"} and toci.K[0].S == Name.P
        ps = [k for k in toci.K if k.S == Name.P]
        assert all(p.P.objgen == toci.objgen for p in ps)
        first = list(ps[0].K)
        assert first[0].S == Name.Link and first[0].P.objgen == ps[0].objgen
        held = [(p, k) for p in ps for k in p.K if not (isinstance(k, Dictionary) and "/S" in k)]
        assert len(held) == len(content)
        # The parent tree names the P for the text it now holds
        arr = number_tree_get(pdf.Root.StructTreeRoot.ParentTree, int(pdf.pages[0].obj.StructParents))
        assert all(arr[int(k.MCID) if isinstance(k, Dictionary) else int(k)].objgen == p.objgen for p, k in held)
        assert str(toci.Ref[0].get("/S", "")).startswith("/H")       # the heading its link goes to
        findings, _ = check_integrity(pdf)
        assert [f for f in findings if f.severity == "error"] == []


def test_a_pdf_2_documents_own_tags_get_what_pdfua2_asks(tmp_path):
    """#413: a document that comes as PDF 2.0 with tags of its own is PDF/UA-2 already, and is given what the step
    gives a PDF/UA-1 file (its Notes as FENote here), noted apart from a conversion, whatever part was chosen."""
    src = tmp_path / "pdf2.pdf"
    with pikepdf.open(_tagged(tmp_path)) as pdf:
        pdf.save(src, force_version="2.0")
    store = JobStore("00000000-0000-4000-8000-000000000413", root=tmp_path / "jobs")
    store.create()
    store.input_pdf.write_bytes(src.read_bytes())
    result = asyncio.run(run_remediation(store, JobOptions(stem="doc", provider="mock")))
    comp = result["reports"]["comparison"]
    assert "pdfua2_changes" not in comp and any("FENote" in c for c in comp["pdf2_repairs"])
    assert comp["after"]["pdfua_part"] == 2
    with pikepdf.open(store.output_dir / result["files"]["remediated_primary"]) as pdf:
        assert any(e.get("/S") == Name.FENote for e in _elems(pdf))


def _run(samples, tmp_path, pdfua):
    store = JobStore("00000000-0000-4000-8000-00000000004" + {"1": "1", "2": "2", "both": "3"}[pdfua], root=tmp_path)
    store.create()
    store.input_pdf.write_bytes((samples / "simple_report.pdf").read_bytes())
    result = asyncio.run(run_remediation(store, JobOptions(stem="doc", provider="mock", pdfua=pdfua)))
    return store, result


def _version(path):
    with pikepdf.open(path) as pdf:
        return str(pdf.pdf_version)


def test_pdfua_2_delivers_the_files_as_pdf_2(samples, tmp_path):
    store, result = _run(samples, tmp_path, "2")
    files = [f for f in result["files"].values() if f]
    assert files and all(_version(store.output_dir / f) == "2.0" for f in files)
    assert "remediated_pdfua2" not in result["files"]
    assert result["reports"]["comparison"]["pdfua2_changes"][0].startswith("saved as PDF 2.0")


def test_both_adds_a_pdfua_2_copy_and_keeps_the_pdfua_1_files(samples, tmp_path):
    store, result = _run(samples, tmp_path, "both")
    assert _version(store.output_dir / result["files"]["remediated_primary"]) < "2.0"
    assert _version(store.output_dir / result["files"]["remediated_pdfua2"]) == "2.0"
    assert "after_pdfua2" in result["reports"]["comparison"]


def test_pdfua_1_is_unchanged(samples, tmp_path):
    store, result = _run(samples, tmp_path, "1")
    assert all(_version(store.output_dir / f) < "2.0" for f in result["files"].values() if f)
    assert "pdfua2_changes" not in result["reports"]["comparison"]


def test_the_tag_check_accepts_pdf_2_types_in_their_namespace(tmp_path):
    """FENote is standard in PDF 2.0's namespace: the engine's own integrity check mustn't fail the PDF/UA-2 file."""
    from app.verification.integrity import check_integrity
    out = tmp_path / "ua2.pdf"
    upgrade(_tagged(tmp_path), out)
    with pikepdf.open(out) as pdf:
        findings, _ = check_integrity(pdf)
        assert [f for f in findings if f.severity == "error"] == []
        # The same type outside that namespace is still unknown
        note = next(e for e in _elems(pdf) if e.get("/S") == Name.FENote)
        del note["/NS"]
        findings, _ = check_integrity(pdf)
        assert any("not standard" in f.issue for f in findings if f.severity == "error")


def test_a_footnote_link_in_a_pdfua2_file_goes_both_ways(tmp_path):
    """#633: in a PDF/UA-2 file a note is FENote. A tag can be pointed at one in the tag manager, and the note names
    the tag back (8.2.5.14); pointing it elsewhere or at nothing takes the old note's name of it away."""
    from app.tagging.editor import apply_edits
    from app.verification.integrity import check_integrity

    ua2 = tmp_path / "ua2.pdf"
    upgrade(_tagged(tmp_path), ua2)
    with pikepdf.open(ua2) as pdf:
        _, index = check_integrity(pdf)
        roles = [(e.index, e.role) for e in index.elements]
    notes = [i for i, r in roles if r == "FENote"]
    para = next(i for i, r in roles if r == "P")

    def refs_of(path):
        with pikepdf.open(path) as pdf:
            _, index = check_integrity(pdf)
            place = {e.obj.objgen: e.index for e in index.elements if e.obj.is_indirect}
            out = {}
            for e in index.elements:
                ref = e.obj.get("/Ref")
                out[e.index] = {place.get(r.objgen) for r in (list(ref) if isinstance(ref, Array) else [])}
            errors = [f for f in check_integrity(pdf)[0] if f.severity == "error"]
            return out, errors

    first, second, third = tmp_path / "a.pdf", tmp_path / "b.pdf", tmp_path / "c.pdf"
    log = apply_edits(ua2, first, [{"op": "link_note", "element": para, "expect_role": "P", "note": notes[0]}])
    assert log == [f"element {para}: points at note {notes[0]}"]
    refs, errors = refs_of(first)
    assert refs[para] == {notes[0]} and para in refs[notes[0]] and errors == []
    apply_edits(first, second, [{"op": "link_note", "element": para, "expect_role": "P", "note": notes[1]}])
    refs, _ = refs_of(second)
    assert refs[para] == {notes[1]} and para in refs[notes[1]] and para not in refs[notes[0]]
    apply_edits(second, third, [{"op": "link_note", "element": para, "expect_role": "P", "note": None}])
    refs, errors = refs_of(third)
    assert refs[para] == set() and para not in refs[notes[1]] and errors == []


def test_a_list_of_dash_bullets_is_unordered_not_none(tmp_path):
    """#631: PDF/UA-2 doesn't allow ListNumbering None on a list whose items have labels (8.2.5.25); PDF 1.7's None for
    dash bullets is PDF 2.0's Unordered."""
    src = _tagged(tmp_path)
    with pikepdf.open(src, allow_overwriting_input=True) as pdf:
        root = pdf.Root.StructTreeRoot
        doc = root.K[0] if isinstance(root.K, Array) else root.K
        lbl = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Lbl, ActualText=String("-")))
        li = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.LI, K=Array([lbl])))
        doc.K.append(pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.L, K=Array([li]),
                                                  A=Dictionary(O=Name.List, ListNumbering=Name("/None")))))
        pdf.save(src)
    out = tmp_path / "ua2.pdf"
    upgrade(src, out)
    with pikepdf.open(out) as pdf:
        lst = next(e for e in _elems(pdf) if e.get("/S") == Name.L)
        assert lst.A.ListNumbering == Name.Unordered


def test_a_destination_on_a_page_drawn_as_a_form_names_its_tag(tmp_path):
    """#631: a page whose content is a form XObject (its tags' content references carry /Stm) still has tags for a
    bookmark to name (8.8)."""
    from app.tagging.pdfua2 import _elems as tree, _structure_destinations

    pdf = pikepdf.new()
    form = pdf.make_stream(b"/P <</MCID 0>> BDC EMC", Type=Name.XObject, Subtype=Name.Form, BBox=[0, 0, 612, 792])
    page = pdf.make_indirect(Dictionary(Type=Name.Page, MediaBox=Array([0, 0, 612, 792]),
                                        Resources=Dictionary(XObject=Dictionary(Fm0=form)),
                                        Contents=pdf.make_stream(b"/Fm0 Do")))
    pdf.Root.Pages = pdf.make_indirect(Dictionary(Type=Name.Pages, Kids=Array([page]), Count=1))
    page.Parent = pdf.Root.Pages
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    heading = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.H1, P=root, Pg=page,
                                           K=Array([Dictionary(Type=Name.MCR, Pg=page, Stm=form, MCID=0)])))
    root.K = Array([heading])
    pdf.Root.StructTreeRoot = root
    item = pdf.make_indirect(Dictionary(Title=String("Start"), Dest=Array([page, Name.Fit])))
    pdf.Root.Outlines = pdf.make_indirect(Dictionary(Type=Name.Outlines, First=item, Last=item, Count=1))
    assert _structure_destinations(pdf, tree(root)) == 1
    assert item.A.SD[0].objgen == heading.objgen and "/Dest" not in item


def _page_with(pdf, contents: bytes, resources: Dictionary):
    page = pdf.make_indirect(Dictionary(Type=Name.Page, MediaBox=Array([0, 0, 612, 792]), Resources=resources,
                                        Contents=pdf.make_stream(contents)))
    pdf.Root.Pages = pdf.make_indirect(Dictionary(Type=Name.Pages, Kids=Array([page]), Count=1))
    page.Parent = pdf.Root.Pages
    return page


def test_a_link_to_a_name_in_the_catalogs_dests_names_its_tag(tmp_path):
    """#615: a LaTeX document's links go to names kept in the catalog's /Dests, a dictionary keyed by name (not the
    /Names tree's strings); each still names the tag it goes to (8.8)."""
    from app.tagging.pdfua2 import _elems as tree, _structure_destinations

    pdf = pikepdf.new()
    page = _page_with(pdf, b"/H1 <</MCID 0>> BDC EMC", Dictionary())
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    heading = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.H1, P=root, Pg=page, K=0))
    root.K = Array([heading])
    pdf.Root.StructTreeRoot = root
    pdf.Root.Dests = pdf.make_indirect(Dictionary({"/section.1": Dictionary(D=Array([page, Name.XYZ, 0, 700, 0]))}))
    link = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link, Rect=Array([0, 0, 10, 10]),
                                        Dest=Name("/section.1")))
    page.Annots = Array([link])
    assert _structure_destinations(pdf, tree(root)) == 1
    assert link.A.SD[0].objgen == heading.objgen and "/Dest" not in link


def _picture_font(pdf):
    """A Type 1 font like TeX's LINE10: its line pieces mapped to private use code points (font_unicode.py)."""
    cmap = (b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap 1 begincodespacerange <00> <FF> "
            b"endcodespacerange 2 beginbfchar <41> <E041> <42> <0042> endbfchar endcmap end end")
    return pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name("/LINE10"),
                                        ToUnicode=pdf.make_stream(cmap)))


def test_an_artifact_drawing_private_use_glyphs_gets_empty_read_aloud_text(tmp_path):
    """#615: veraPDF counts glyphs inside /Artifact marked content as real content under PDF/UA-2 8.4.3, so an artifact
    that draws TeX's line pieces (private use code points) gets ActualText "". Others are left as they are, and so is
    tagged content (its tag's read-aloud or alt text covers it)."""
    from app.tagging.pdfua2 import _artifacts_with_private_use

    pdf = pikepdf.new()
    font = _picture_font(pdf)
    form = pdf.make_stream(b"BT /F1 10 Tf (A) Tj ET", Type=Name.XObject, Subtype=Name.Form, BBox=[0, 0, 10, 10],
                           Resources=Dictionary(Font=Dictionary(F1=font)))
    page = _page_with(pdf, b"BT /F1 10 Tf "
                           b"/Artifact <</Type /Layout>> BDC [(A) 5 (A)] TJ EMC "  # line pieces: read-aloud text
                           b"/Artifact BMC (A) Tj EMC "                         # the same, with no property list
                           b"/Artifact BMC (B) Tj EMC "                         # a readable glyph: left alone
                           b"/Figure <</MCID 0>> BDC (A) Tj EMC ET "            # tagged: its Figure's alt covers it
                           b"/Artifact BMC /Fm0 Do EMC",                        # a form that draws them
                      Dictionary(Font=Dictionary(F1=font), XObject=Dictionary(Fm0=form)))
    assert _artifacts_with_private_use(pdf) == 3
    ops = [(str(op), [str(o) if not isinstance(o, Dictionary) else dict(o) for o in operands])
           for operands, op in pikepdf.parse_content_stream(page)]
    marks = [o for o in ops if o[0] in ("BDC", "BMC")]
    assert marks[0] == ("BDC", ["/Artifact", {"/Type": Name.Layout, "/ActualText": String("")}])
    assert marks[1] == ("BDC", ["/Artifact", {"/ActualText": String("")}])
    assert marks[2] == ("BMC", ["/Artifact"])
    assert marks[3][1][0] == "/Figure" and "/ActualText" not in marks[3][1][1]
    assert marks[4] == ("BDC", ["/Artifact", {"/ActualText": String("")}])
    assert form.read_bytes() == b"BT /F1 10 Tf (A) Tj ET"  # its own content is unchanged


def test_a_link_to_a_place_the_document_doesnt_have_is_dropped_and_reported(tmp_path):
    """#615: a link to a name the document never defines (LaTeX's page.xiii, say) went nowhere, and fails PDF/UA-2
    8.8 however it's written: in the PDF/UA-2 file it loses its destination, and the report says which, and where."""
    from app.tagging.pdfua2 import _dead_links, _elems as tree, _structure_destinations

    pdf = pikepdf.new()
    page = _page_with(pdf, b"/H1 <</MCID 0>> BDC EMC", Dictionary())
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    root.K = Array([pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.H1, P=root, Pg=page, K=0))])
    pdf.Root.StructTreeRoot = root
    pdf.Root.Dests = pdf.make_indirect(Dictionary({"/section.1": Array([page, Name.Fit])}))

    def link(**kw):
        return pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link, Rect=Array([0, 0, 10, 10]), **kw))
    good, named, acted = link(Dest=Name("/section.1")), link(Dest=Name("/page.xiii")), \
        link(A=Dictionary(S=Name.GoTo, D=String("page.xii")))
    page.Annots = Array([good, named, acted])
    dropped: list = []
    assert _structure_destinations(pdf, tree(root), dropped) == 1
    assert "/A" in good and "/Dest" not in named and "/A" not in named and "/A" not in acted
    assert dropped == [(1, "page.xiii"), (1, "page.xii")]
    assert _dead_links(dropped) == ("2 link(s) or bookmark(s) went to places the document doesn't have (page.xiii, "
                                    "page.xii) and now go nowhere, on page(s) 1: fix them in the source document")
