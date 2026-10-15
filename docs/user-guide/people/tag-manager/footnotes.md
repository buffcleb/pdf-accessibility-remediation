# Footnotes

[The tag manager](README.md) › Footnotes

The tool tags a footnote as a **Note** when the text has a raised mark ("fee¹") and the small print at the foot of the
page starts with the same mark. The mark becomes a **Reference** that points at its note, so a screen reader can read
it as a footnote and go to it.

## When a mark isn't linked

1. If the note itself wasn't found, select its text and change its type to **Note** ([Changing a tag's
   type](editing-a-tag.md#changing-a-tags-type)). It's given the ID a note needs.
2. Right-click the mark (or the text it's in) and choose **Footnote → Points at "…"**. The notes on that page and the
   pages either side are listed.

![The tag menu on a footnote Reference in the tag tree, with the Footnote submenu open: Points at "1 The state fee is
set each year by the", with the note "It already points at this note."](../../../images/tag-manager/footnote.png)

**Doesn't point at a footnote** takes the link away. The tool links the footnotes it finds itself.

## Notes under a table

A note under a table that starts with a symbol (*, †) used in the table's cells is tagged the same way, and the cell
reads "500 (footnote asterisk)".
