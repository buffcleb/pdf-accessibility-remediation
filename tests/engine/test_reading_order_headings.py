"""#297: the AI's reading order can't read a heading after the text drawn under it in its column."""
from app.pipeline.enrichment import headings_first
from tests.engine.layout import block, page


def _page(*blocks):
    return page(*(block(i, bbox, i, role) for i, role, bbox in blocks))


GUIDE = _page(("org", "P", (72, 67, 179, 84)), ("title", "H1", (72, 86, 401, 117)),
              ("intro", "P", (72, 130, 532, 178)), ("h2", "H2", (72, 199, 185, 220)), ("body", "P", (72, 225, 523, 240)))


def test_a_title_read_after_the_text_under_it_comes_first_again():
    order, moved = headings_first(GUIDE, ["org", "intro", "title", "h2", "body"])
    assert order == ["org", "title", "intro", "h2", "body"] and moved == 1


def test_the_rest_of_the_ai_order_stays():
    # Nothing to put right: the order comes back as it was
    assert headings_first(GUIDE, ["org", "title", "intro", "h2", "body"]) == (["org", "title", "intro", "h2", "body"], 0)
    # Text above the heading may come after it (a running head the AI put last, say)
    assert headings_first(GUIDE, ["title", "intro", "h2", "body", "org"])[1] == 0


def test_another_column_is_not_under_the_heading():
    # A heading at the foot of the left column; the right column, lower on the page, is read first
    page = _page(("right", "P", (320, 300, 540, 500)), ("h", "H2", (72, 600, 290, 620)),
                 ("left", "P", (72, 625, 290, 700)))
    assert headings_first(page, ["right", "h", "left"]) == (["right", "h", "left"], 0)
    # ...but a heading across both columns comes before both
    wide = _page(("left", "P", (72, 300, 290, 500)), ("right", "P", (320, 300, 540, 500)),
                 ("h", "H1", (72, 250, 540, 280)))
    assert headings_first(wide, ["left", "h", "right"]) == (["h", "left", "right"], 1)
