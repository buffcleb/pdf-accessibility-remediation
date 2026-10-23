"""#423: text drawn inside a Form XObject (a page placed as a reusable drawing) is tagged through /Stm references.
Its tags get their boxes in the tag map, and the checks read their text, kept apart from the page's own marked
content, whose numbers can be the same."""
import pikepdf

from app.config import get_settings
from app.pipeline.analyzer import analyze_document
from app.tagging.tagger import tag_document
from app.verification.integrity import check_integrity
from app.verification.semantic import SemanticChecker
from app.verification.tagmap import build_tagmap

LINES = ["Results of the survey", "Most people answered within a week.", "A second paragraph follows here."]


def _form_page(tmp_path):
    """A page whose body text is drawn by a Form XObject (as some producers lay out every page), with a heading in
    the page's own stream."""
    pdf = pikepdf.new()
    font = pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1,
                                                BaseFont=pikepdf.Name.Helvetica,
                                                Encoding=pikepdf.Name.WinAnsiEncoding))
    fonts = pikepdf.Dictionary(F1=font)
    body = "".join(f"BT /F1 11 Tf 72 {600 - 40 * n} Td ({t}) Tj ET\n" for n, t in enumerate(LINES[1:]))
    form = pdf.make_stream(body.encode(), Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Form,
                           BBox=[0, 0, 612, 792], Resources=pikepdf.Dictionary(Font=fonts))
    page = pikepdf.Dictionary(Type=pikepdf.Name.Page, MediaBox=[0, 0, 612, 792],
                              Resources=pikepdf.Dictionary(Font=fonts, XObject=pikepdf.Dictionary(Fm0=form)),
                              Contents=pdf.make_stream(f"BT /F1 20 Tf 72 700 Td ({LINES[0]}) Tj ET\n/Fm0 Do\n".encode()))
    pdf.pages.append(pikepdf.Page(page))
    src = tmp_path / "form.pdf"
    pdf.save(src)
    return src


def _tagged(tmp_path):
    src = _form_page(tmp_path)
    out = tmp_path / "tagged.pdf"
    tag_document(src, out, analyze_document(src), title="Survey", language="en-US")
    with pikepdf.open(out) as pdf:
        forms = [x for x in pdf.pages[0].Resources.XObject.values() if x.get("/Subtype") == "/Form"]
        assert forms and b"/MCID" in forms[0].read_bytes()  # the body is marked inside the form
    return out


def test_tags_on_content_inside_a_form_get_their_boxes_and_text(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "page_workers", 1)
    out = _tagged(tmp_path)
    tagmap = build_tagmap(out)
    text_tags = [e for e in tagmap["elements"] if e["role"] in ("H1", "H2", "P") and e.get("own")]
    assert len(text_tags) >= 3
    assert all(e.get("boxes") for e in text_tags), [e["role"] for e in text_tags if not e.get("boxes")]
    with pikepdf.open(out) as pdf:
        _, index = check_integrity(pdf)
        checker = SemanticChecker(out, pdf, index)
        checker._collect()
        said = sorted(" ".join(checker.own[e.index].text.split()) for e in index.elements if e.index in checker.own)
    # Each line once, in its own tag: the form's marked content isn't taken for the page's, whose ids are the same
    for line in LINES:
        assert sum(line in t for t in said) == 1, (line, said)
    assert checker.by_form_mcid and checker.by_mcid


def test_the_same_with_the_pages_shared_out_over_processes(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "page_workers_min_pages", 1)
    out = _tagged(tmp_path)

    def read(workers):
        monkeypatch.setattr(get_settings(), "page_workers", workers)
        with pikepdf.open(out) as pdf:
            _, index = check_integrity(pdf)
            c = SemanticChecker(out, pdf, index)
            c._collect()
            return repr((c.own, c.by_mcid, c.by_form_mcid, dict(c.artifact)))

    assert read(2) == read(1)
