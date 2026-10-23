# Finding your way around

[The tag manager](README.md) › Finding your way around

The viewing controls sit in the header row of each side, beside its version picker: **View** and **Show** on the
left, the zoom and **Scroll together** on the right. At the top of the page, on the right: **Actions** (with a tag
selected), **Undo** and **Redo**, **Unsaved changes…**, **Document…**, **Panel**, **Read aloud** and, on your own
documents, **Report a problem**.

## The two sides

- **Version pickers** above each side choose what's shown: the original, the remediated file, a saved version,
  or **Tag tree**. The left side starts as the tag tree; pick a version there to compare documents instead.
- **The divider** between the two sides can be dragged to give one more room (or, with the keyboard: Tab to it,
  then the left and right arrow keys; Enter puts it back in the middle). A second divider, before the editing
  panel, sets that panel's width the same way. Your browser remembers where you left both.
- **Zoom:** 50% to 300%, or fit the width. **Scroll together** keeps both sides in step.
- **Which page the editing panel shows:** the page taking up most of the right-hand side, or the page you click.

## The tag tree

*Tag tree* on the left shows the right-hand version's tags as a tree. Clicking a tag jumps to it on the page and
opens it for editing, and the tree follows as you scroll. **Expand all** and **Collapse all** open or close every
branch. **About**, in the tree's header, unfolds what the list shows and how to move tags in it; it stays as you
leave it.

![The tag tree beside the second page of the parking guide. The tree lists the table's rows and cells, a paragraph
with a footnote Reference, a Link, a Note, then the section "Where to park" with its H2, a Figure whose alt text
begins "Bar chart showing permit sales by lot", and a Caption. The Figure row is selected and the bar chart is
outlined on the page.](../../../images/tag-manager/tree.png)

Each row shows the tag's type, its text (or a figure's alt text, or the name a screen reader announces for a field
or link), and its page. The same pane can show **Reading order** instead of the tree (the choice at its top): see
[Reading order](reading-order.md).

- **Original tags:** when a document arrived already tagged and its tags were kept, the tree shows which tags were
  its own: a light blue row with the word **original**. The others were added by this service (fields and links that
  had no tag, content the original tags left out). The editing panel says the same for the selected tag. Documents
  that arrived untagged, or whose tags had to be rebuilt, show no marks.
- **Tags with unsaved changes** are tinted and say what changed ("moved", "type changed", "content added"…).

## What's drawn on the page

> **Changed 22 October 2026:** a tag's details are always shown in full, inside the pane, and a cut-off row in the
> tag tree has **Read all**.

- **Show:** flagged tags (the default), all tags, the reading order (numbered), or no overlay.
- **Form fields and links** are tags too: they're outlined where they sit, numbered in the reading order, and shown
  with the name a screen reader announces ("field: Enter account number").
- **Hover over or focus a tag** for its details, including what a screen reader will read. The details show
  everything in full (alt text, read-aloud text, a paragraph's words), above the tag or below it, always inside the
  pane; a long one scrolls, and Tab from the tag goes into it. Clicking blank page hides the details.
- **Long text in the tag tree** is cut short on its row, with the whole of it in the row's tooltip (and read in full
  by a screen reader). **Read all**, beside the cut-off words, selects the tag and opens its details.
- **Clicking a tag on the page** selects the innermost tag under the pointer, even inside an outlined list or
  section: a list item's text, not the list. Click the same place again to go one tag out (the item, then the list,
  then what holds it), and once more after the outermost to come back in.

## An item from the review list

> **New 22 October 2026:** **Show** on a review item highlights the area it's about.

**Show** on an item in the document's **Needs review** list opens the tag manager at the item's page:

- the item's tag is selected, when it has one;
- the area the item is about is outlined (solid and dashed) and the rest of the page is dimmed; an item about the
  whole page (reading order, right-to-left text) outlines the page;
- a label names the item. Its **×**, or Escape, closes the highlight.

Items that no tag holds (text hidden as an artifact, a decorative image, faint text, an AI reading) are shown this way
too. On a page with such items, **N page-level notes**, in the page's top-left corner, lists them; choosing one highlights it in
the same way.

## One page or all pages

> **Changed 22 October 2026:** the page turning looks like a PDF viewer's: up and down arrows, then the page number.

**View** shows **All pages** in one scroll (the default), or **One page** at a time, which keeps the tag tree short
in a long document. In One page, turn the page as in a PDF viewer: the up arrow (**Previous page**) and the down
arrow (**Next page**), or type a page number in the box after them and press Enter (Escape puts the page back), or
press **[** and **]**. Both sides show the same page, and the tag tree and reading order show only that page's tags.
Choosing a tag on another page (from a finding, or while reading aloud) turns to its page.

A document of more than 50 pages opens in **One page**, because drawing every page at once takes a while; choosing
**All pages** there asks first. In One page, choosing a tag in the tree that spans several pages and starts on
another one asks before turning the page: go to where it starts, or select it and stay (when part of it is on the
page you're on).

## Isolating a tag

**Isolate this tag** (in the tag menu) veils everything else, in the tag tree and on the page, so only that tag and
the tags inside it stand out. The rest can still be clicked. **Show everything** at the top of the page, or in the
menu, ends it.

![The parking guide with its table isolated. In the tag tree, every row is grayed out except the Table and the rows
and cells inside it; on the page, everything but the table is veiled. A details box under the table shows its
text. A "Show everything" button is at the top of the page.](../../../images/tag-manager/isolate.png)
