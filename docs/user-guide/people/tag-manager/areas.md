# Areas: content in the wrong tag, or not tagged

[The tag manager](README.md) › Areas

An area is a box you draw on the page to see exactly what's inside it: which tag each word or drawing belongs to,
and what isn't tagged at all. Use it for a word the tool put in the wrong paragraph, a chart label that belongs with
its figure, or content screen readers skip that they shouldn't.

## Drawing an area

Choose **Draw an area on the page** (in the tag menu, or by right-clicking the page where there is no tag) and drag a
box over the right-hand page. From the keyboard, use **Use all of page N**. What's inside opens in a window as soon as
it's drawn or the page chosen; a tag named in its list can be selected from there. **Done** closes it and clears the
area, and **Clear the area** removes it.

![The Areas window over page 2 of a lab safety checklist, where a box has been drawn round an evacuation diagram.
"Tagged content in the area on page 2 (4)": the text "Corridor" in a TD, "Stairs B" in a TD, "Eyewash and safety
shower" in a paragraph, and the figure's caption in a paragraph, each checked, with the button "Move checked content
into the selected tag". "Untagged content (artifacts) in the area (10)": ten drawings, two of them unchecked because
they go outside the area. Below: "Tag the checked items as" set to Paragraph, and Tag checked
items.](../../../images/tag-manager/area.png)

The picture shows a common case: a diagram drawn with lines and labels, read as table cells, paragraphs and
untagged drawings rather than as one figure.

## Tagged content

Select the tag that should hold it (on the page or in the tag tree), check the content, then **Move checked content
into the selected tag**. Content can only go into a tag that holds content (a paragraph, heading, figure, cell or list
item body), not into a table or list itself. A tag left with nothing is removed.

## Untagged content

Untagged content is an artifact, which screen readers skip. **Tag checked items** makes the checked items one new tag
(paragraph, heading, figure with alt text, and so on), read right after the tag above the area. Move it in the tag tree
afterwards if needed.

An item that extends outside the box, or sits inside another untagged item, can't be tagged on its own; draw a larger
box.

## The new tag, before you save

> **New 22 October 2026:** a new tag can be chosen and changed before it's saved.

A tag made by **Tag checked items**, or the new part of a [split](merge-split-delete.md#splitting), is chosen as soon
as it's made. A bar above the pages, **New tag (not saved)**, shows its type and words, with:

- **Type:** pick another, then **Change**;
- **Alt text,** for a figure or formula: it needs some before the change applies;
- **Take back:** the new tag goes, as if you hadn't made it;
- **New tag:** with several new tags, move to another one.

**Close** (or Escape) closes the bar; click the new tag in the tag tree or on the page to choose it again. Each change
is part of the edit that made the tag, so **Undo** takes it back like any other change. A figure's new parts, from
[splitting a figure](figures.md#splitting-a-figure), can't be chosen this way yet: change them after saving.

## Part of a figure

Drawing an area over part of a figure offers **Make them a figure of their own…** for the pieces inside it. See
[Splitting a figure](figures.md#splitting-a-figure).
