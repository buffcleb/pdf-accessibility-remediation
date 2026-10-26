"""Headings on flattened pages: the second look reads a heading and the paragraph under it as one block."""
from app.pipeline.transfer import transfer_enrichment
from tests.engine.layout import block, document, page


def _page(blocks):
    return document(page(*blocks, width=600, height=800))


def _block(i, bbox, text, role="P", bold=False):
    return block(f"b{i}", bbox, text, role, bold=bold)


def test_a_block_holding_a_heading_and_its_paragraph_is_divided_where_they_were():
    first = _page([_block(0, (70, 100, 400, 114), "3.2. Ways to store the samples", "H3", True),
                   _block(1, (100, 116, 380, 160), "3.2.1. Cold rooms 3.2.2. Other places")])
    again = _page([_block(0, (70, 99, 401, 161), "3.2. Ways to store the samples 3.2.1. Cold rooms 3.2.2. Other places")])
    transfer_enrichment(first, again, {0}, set())
    page = again.pages[0]
    assert [(b.role, b.text) for b in page.blocks] == [("H3", "3.2. Ways to store the samples"),
                                                       ("P", "3.2.1. Cold rooms 3.2.2. Other places")]
    # The pieces tile the block: no line of it is left outside both
    assert page.blocks[0].bbox[1] == 99 and page.blocks[1].bbox[3] == 161
    assert page.blocks[0].bbox[3] == page.blocks[1].bbox[1]
    assert len({b.id for b in page.blocks}) == 2 and set(page.reading_order) == {b.id for b in page.blocks}


def test_blocks_are_left_alone_without_a_heading_among_them_or_on_a_page_that_was_not_flattened():
    def pair(role):
        return _page([_block(0, (70, 100, 400, 114), "First line", role), _block(1, (70, 116, 400, 160), "Second part")])

    merged = lambda: _page([_block(0, (70, 99, 401, 161), "First line Second part")])  # noqa: E731
    again = merged()
    transfer_enrichment(pair("P"), again, {0}, set())
    assert len(again.pages[0].blocks) == 1
    again = merged()
    transfer_enrichment(pair("H2"), again, set(), set())
    assert len(again.pages[0].blocks) == 1


def test_blocks_side_by_side_are_not_divided():
    first = _page([_block(0, (70, 100, 290, 160), "Left heading", "H2", True), _block(1, (310, 100, 530, 160), "Right text")])
    again = _page([_block(0, (70, 100, 530, 160), "Left heading Right text")])
    transfer_enrichment(first, again, {0}, set())
    assert len(again.pages[0].blocks) == 1
