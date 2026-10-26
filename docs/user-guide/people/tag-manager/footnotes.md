# Footnotes

[The tag manager](README.md) › Footnotes

The tool tags a footnote as a **Note** when the text has a raised mark ("fee¹") and the small print at the foot of the
page starts with the same mark. The mark becomes a **Reference** that points at its note, so a screen reader can read
it as a footnote and go to it.

## Endnotes

**New 24 October 2026** (#285). Notes gathered at the end of a document or chapter, under a heading such as **Notes**
or **Endnotes**, are tagged as notes too, when the first one is numbered 1. In a book whose notes start again at 1 for
each chapter, each chapter's label ("Chapter 1") becomes a heading. The raised marks in the text point at their
endnotes, pages away, and a screen reader says "endnote 1". A note that runs onto the next page stays one note, and
the notes pages' running headers ("Notes to Pages 17–30") are left out of the reading.

The marks are matched to the notes by number, chapter by chapter: the tool links them only when the marks start
again at 1 as many times as the notes do. Otherwise the notes are still tagged, but the marks aren't linked to them.

## When a mark isn't linked

1. If the note itself wasn't found, select its text and change its type to **Note** ([Changing a tag's
   type](editing-a-tag.md#changing-a-tags-type)). It's given the ID a note needs.
2. Right-click the mark (or the text it's in) and choose **Footnote → Points at "…"**. The notes on that page and the
   pages either side are listed, so an endnote pages away can't be chosen here.

![The tag menu on a footnote Reference in the tag tree, with the Footnote submenu open: Points at "1 The state fee is
set each year by the", with the note "It already points at this note."](../../../images/tag-manager/footnote.png)

**Doesn't point at a footnote** takes the link away. The tool links the footnotes it finds itself.

**Changed 25 October 2026** (#633). In a file delivered as PDF/UA-2, notes are tagged **FENote**, PDF 2.0's note, and
the tag tree shows them so. Linking works the same way, and the note names the mark back, as PDF/UA-2 asks.

## Notes under a table

A note under a table that starts with a symbol (*, †) used in the table's cells is tagged the same way, and the cell
reads "500 (footnote asterisk)".
