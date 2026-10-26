"""Analyses built by hand, and small drawn pages to analyse: the pages and blocks the structure tests start from.
A helper module, not a conftest, so a test imports what it uses (from tests.engine.layout import block, page)."""
import pymupdf as fitz

from app.pipeline.models import DocumentAnalysis, ImageRegion, PageAnalysis, TableRegion, TextBlock


def block(id, bbox, text, role="P", size=11.0, bold=False, page=0, **fields) -> TextBlock:
    """One text block; any other TextBlock field by name (actual_text=..., has_scripts=True)."""
    return TextBlock(id=id, page=page, bbox=bbox, text=text, font_size=size, role=role, bold=bold, **fields)


def page(*elements, width=612, height=792, number=0, order=None, **fields) -> PageAnalysis:
    """A page holding the blocks, images and tables given, in that order. The reading order is `order` when given,
    else left empty, as a page made by hand has it."""
    p = PageAnalysis(number=number, width=width, height=height, rotation=0,
                     pdf_to_page=(1, 0, 0, -1, 0, height), **fields)
    for e in elements:
        if isinstance(e, TextBlock):
            p.blocks.append(e)
        elif isinstance(e, ImageRegion):
            p.images.append(e)
        elif isinstance(e, TableRegion):
            p.tables.append(e)
        else:
            raise TypeError(f"not a page element: {e!r}")
    if order is not None:
        p.reading_order = list(order)
    return p


def document(*pages, body=10.0, title=None, language=None, **fields) -> DocumentAnalysis:
    """An untagged document of the pages given."""
    return DocumentAnalysis(page_count=len(pages), pages=list(pages), title=title, language=language,
                            has_struct_tree=False, is_marked=False, body_font_size=body, **fields)


def draw_pdf(path, lines, width=612, height=792, size=11, fontname="helv", fontfile=None):
    """A one-page PDF of (x, y, text) lines, or (x, y, text, size); with `fontfile`, in that font, embedded."""
    doc = fitz.open()
    p = doc.new_page(width=width, height=height)
    if fontfile:
        fontname = "emb"
        p.insert_font(fontname=fontname, fontfile=fontfile)
    for x, y, text, *rest in lines:
        p.insert_text((x, y), text, fontsize=rest[0] if rest else size, fontname=fontname)
    doc.save(path)
    return path
