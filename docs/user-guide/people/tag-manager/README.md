# The tag manager

[Guide for people submitting documents](../README.md) › The tag manager

The tag manager is where you check a remediated document's tags against its pages and fix what's wrong. It shows
the remediated file on the right, with its tags drawn over the pages, and that file's tag tree on the left. The
left side can show another version instead (your original, say) to compare the two side by side.

Open it with **Open in tag manager** on the document's page, or **Show** on an item in the review list, which
highlights the area the item is about ([more](finding-your-way.md#an-item-from-the-review-list)).

While a document opens, each side shows how far it has got: the document and its tags downloading (with how many
megabytes have come), then reading the tags, then drawing the first page. A large document (over 100 pages or
20 MB) can take a minute or two; refreshing the page starts it again from the beginning, so it's best to wait.

![The tag manager for a two-page parking guide. On the left, the original document, marked "111 failed checks".
In the middle, the remediated version, marked "passes", with each tag outlined and labeled on the page: P, H1, H2,
LBody, TH, TD, Reference, Link and Note. On the right, the editing panel shows the selected Heading 1 with its tag
type, read-aloud text and language fields.](../../../images/tag-manager/overview.png)

## Contents

1. [Finding your way around](finding-your-way.md): the two sides, the tag tree, what's drawn on the page, one page
   or all pages, isolating a tag
2. [Hearing it read aloud](read-aloud.md): checking order and alt text by ear
3. [Editing a tag](editing-a-tag.md): the editing panel, the tag menu, changing a tag's type, alt text, read-aloud
   text and language, selecting several tags, the document's title and language
4. [Reading order](reading-order.md): checking it and moving tags
5. [Merging, splitting and deleting](merge-split-delete.md)
6. [Lists and tables of contents](lists-and-contents.md): building a list from an outline, nesting items, making a
   table of contents
7. [Figures](figures.md): alt text, decorative figures, splitting a figure into parts
8. [Tables](tables.md): the table editor (headers, caption, merging and splitting cells, rows and columns, making a
   table of paragraphs), header cells and the headers a cell is read with
9. [Areas: content in the wrong tag, or not tagged](areas.md)
10. [Footnotes](footnotes.md): linking a mark to its note
11. [Saving your changes](saving.md): versions, AI review, changes kept between visits
12. [Reporting a problem, and tips for checking](reporting-and-tips.md)

## Who can edit

> **Changed 22 October 2026:** a new tag made by an unsaved change (from an area, or by a split) can be chosen at
> once, and its type or alt text changed before you save.

You can edit your own documents, and group admins can edit their group's. Everyone else who can open a document,
super admins included, sees it read-only. **Nothing is in the file until you save**: every change collects under
**Unsaved changes**, where it can be undone, and saving makes a new version. The original results are never changed.
Each change shows straight away, in the tag tree and on the page, the way it will be once saved, marked **not
saved**: a new tag made from an area or by a split appears where it will be read, and content moved into a tag
shows beside it while the tag it came from shrinks (or goes, if nothing is left in it). A new tag is chosen as soon
as it's made, so you can change its type or alt text straight away ([The new tag](areas.md#the-new-tag-before-you-save)).

## A quick check, in five minutes

1. Set **Show** to *Flagged tags* (the default) and look at what's outlined: these are the tags the review list
   points at.
2. Hover over each figure and read its alt text against the image ([Figures](figures.md)).
3. Switch **Show** to *Reading order* and follow the numbers down each page ([Reading order](reading-order.md)).
4. Press **Read aloud** and listen to a page or two ([Hearing it read aloud](read-aloud.md)).
5. Fix what's wrong, then **Unsaved changes… → Save** ([Saving](saving.md)).
