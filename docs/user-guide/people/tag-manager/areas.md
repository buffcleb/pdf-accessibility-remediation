# Areas: content in the wrong tag, or not tagged

[The tag manager](README.md) › Areas

An area is a box you draw on the page to see exactly what's inside it: which tag each word or drawing belongs to,
and what isn't tagged at all. Use it for a word the tool put in the wrong paragraph, a chart label that belongs with
its figure, or content screen readers skip that they shouldn't.

## Drawing an area

Choose **Draw an area on the page** (in the tag menu, or by right-clicking the page where there is no tag) and drag a
box over the right-hand page. From the keyboard, use **Use all of page N**. What's inside opens in a window as soon as
it's drawn or the page chosen; a tag named in its list can be selected from there. To see the page under the window,
drag it by its title, or use **Move to the side** (press it again for the other side, then the middle). (**New 27
October 2026**)

The window shows one part at a time, in tabs (**Updated 28 October 2026**):

- **Tagged content**, with how many pieces are in the area: move them into another tag, or make part of a figure a
  figure of its own. The window opens here, unless the area holds only untagged content;
- **New tag**: tag what isn't read (untagged content), or make a part of a picture a figure;
- **Suggest tags with AI**, when Describe with AI is on for you. It never opens on its own, as it's an AI call.

Choose a tab with the mouse, or with the arrow keys once one has focus (Home and End go to the first and last). What
you've checked, typed or been suggested in one tab is still there when you come back to it.

Making a tag is the end of it: once a new tag is made (**Tag checked items**, **Make it a figure**, **Add these
tags**), the window closes and the new tag is chosen, [ready to check](#the-new-tag-before-you-save). Moving content
into a tag keeps it open. To close it without making anything, use its **×** or press Escape; either clears the area.
**Clear the area** removes the area and keeps the window. (**Updated 28 October 2026**)

![The Areas window over page 2 of a lab safety checklist, where a box has been drawn round an evacuation diagram.
"Tagged content in the area on page 2 (4)": the text "Corridor" in a TD, "Stairs B" in a TD, "Eyewash and safety
shower" in a paragraph, and the figure's caption in a paragraph, each checked, with the button "Move checked content
into the selected tag". "Untagged content (artifacts) in the area (10)": ten drawings, two of them unchecked because
they go outside the area. Below: "Tag the checked items as" set to Paragraph, and Tag checked
items.](../../../images/tag-manager/area.png)

The picture shows a common case: a diagram drawn with lines and labels, read as table cells, paragraphs and
untagged drawings rather than as one figure.

## Suggest tags with AI

> **New 27 October 2026**

When a part of the page is tagged badly all over (a poster, a diagram whose labels were read as paragraphs), ask the
AI instead of fixing each piece. With **Describe with AI** on for you, **Suggest tags with AI** shows the AI the area
you drew and the content in it, and it suggests the tags a screen reader should read, in reading order: headings,
paragraphs, figures with their labels inside them and alt text, captions. It's one AI call at your document's AI
level.

A figure can be a part of a picture. On a poster or a diagram printed as one picture, the AI can make each board,
device or drawing on it a figure of its own, outlined and numbered on the page so you can check where it is. The
drawings are found by the tag manager from the picture's ink and shown to the AI, which chooses among them, so a
figure's outline is the drawing itself. (**Updated 28 October 2026**) The
page doesn't change: that part of the picture is drawn again, exactly over itself, as the figure's content.
(**Updated 27 October 2026**)

It's in the window's **Suggest tags with AI** tab. (**Updated 28 October 2026**)

Nothing changes yet. Check each suggested tag: untick one you don't want (a figure outlined in the wrong place, say),
change its type, and correct a figure's alt text. **Add these tags** puts them in your unsaved changes as one change,
which you can undo there, and closes the window with the first of them chosen. (**Updated 28 October 2026**) They're read where the first of their content was. Content they take leaves its old tag,
and a tag left with nothing is removed. What the AI took for decoration, or didn't place, stays as it was. The page
itself shows the new tags once you save.

To redo a page's tags from scratch, delete them first, then draw an area and ask: the content of tags you've deleted
is offered to the AI too, before you save. (**Updated 27 October 2026**) You can also tag it again yourself, in the
other tabs. (**New 29 October 2026**)

## A part of a picture

> **New 27 October 2026**

When the area you draw lies over a picture, **A part of a picture**, in the **New tag** tab, makes just that part a figure of its own: give
its alt text (or use **Describe with AI**), then **Make it a figure**. Draw one area per figure, for each board of an
assembly poster, say.

## Tagged content

In the **Tagged content** tab, select the tag that should hold it (on the page or in the tag tree), check the content, then **Move checked content
into the selected tag**. Content can only go into a tag that holds content (a paragraph, heading, figure, cell or list
item body), not into a table or list itself. A tag left with nothing is removed.

Content of a tag you've deleted (or marked as an artifact) in your unsaved changes is listed too, saying so: it stops
being read once you save, so you can give it to another tag now, or make it a new tag in the **New tag** tab, where it's
listed with the untagged content. Both go into the same save as the deletion. (**New 29 October 2026**)

## Untagged content

Untagged content is an artifact, which screen readers skip. In the **New tag** tab, **Tag checked items** makes the checked items one new tag
(paragraph, heading, figure with alt text, and so on), read right after the tag above the area. Move it in the tag tree
afterwards if needed. For a figure or formula, **Describe with AI** (when it's on for you) shows the AI the area you
drew and fills in the alt text for you to check. (**New 27 October 2026**)

An item that extends outside the box, or sits inside another untagged item, can't be tagged on its own; draw a larger
box.

## The new tag, before you save

> **New 22 October 2026:** a new tag can be chosen and changed before it's saved.

A tag made by **Tag checked items**, **Make it a figure**, **Add these tags** or the new part of a
[split](merge-split-delete.md#splitting) is chosen as soon as it's made. It's a tag like any other before you save
(**Updated 28 October 2026**): it has its row in the tag tree, marked **new tag**, and its outline on the page,
and its menu and the editing panel do everything they do for a saved tag. Move it, nest it into a section or put
another tag into it (**Move into**), merge it, split it, delete it, set its language or read-aloud text, or open
**Edit tag** on it. Other tags can be placed next to it or moved into it too. All of it goes into one save.

The same goes for a figure's new parts, from [splitting a figure](figures.md#splitting-a-figure), and for the
heading, list and items of a [list built from lines](lists-and-contents.md), and the table, rows and cells of a
[table made or edited](tables.md): they're tags of their own as soon as the change is made, so a cell can be given
its headers or language, or an item moved, before you save. (**New 29 October 2026**) A table you're editing opens in
the table editor again as it is; one made from text tags opens there only once it's saved (to change its layout now,
undo it under **Unsaved changes** and make it again).

While a new tag is chosen, a bar above the pages, **New tag (not saved)**, shows its type and words, with:

- **Type:** pick another, then **Change**;
- **Alt text,** for a figure or formula: it needs some before the change applies;
- **Take back:** the new tag goes, as if you hadn't made it. The changes you made to it since (a move, its language)
  go with it, and the message says how many; **Undo** brings them all back;
- **New tag:** with several new tags, move to another one.

**Close** (or Escape) closes the bar; the tag stays chosen. Each change on the bar is part of the edit that made the
tag, so **Undo** takes it back like any other change. Taking an edit that makes a tag out of **Unsaved changes** takes
the changes made to that tag with it too. The bar is for tags made from an area, a split or a figure's parts; the tags
of a list or table being built are changed from their menu and the editing panel.

## Part of a figure

Drawing an area over part of a figure offers **Make them a figure of their own…** for the pieces inside it. See
[Splitting a figure](figures.md#splitting-a-figure).
