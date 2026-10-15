# Tables

[The tag manager](README.md) › Tables

A screen reader announces a table's size, then reads each cell with its headers, so a number makes sense on its own.
In the tag tree a table holds rows (TR), and each row holds header cells (TH) and data cells (TD).

## Header cells

Change a cell between header and data with its **Tag type**. Changing it keeps how many rows and columns it spans.

In a table with more than one level of headings, the tool links each cell to its headings by name, and the links
follow such a change: a header made a data cell stops being anyone's heading, and a cell made a header becomes the
heading of the cells below it (a column header) or to its right (a row header).

## The headers a cell is read with

**Edit tag…** on a table cell lists the header cells of its table under **Read with these headers**, with the ones
it's linked to ticked. A header that's under another says so ("Q2 · under 2026").

![The Edit tag window for the data cell "$145" on page 1. Under "Read with these headers": "A screen reader can say
these before the cell. With none ticked, it goes by each header's scope", with three unticked boxes: "Permit",
"Semester" and "Year". Below: the Read aloud as and Language fields, and Add change, Delete this tag… and
Close.](../../../images/tag-manager/table-headers.png)

Tick and untick them, then **Add change**. Untick them all to go back to each header's scope: in a simple table like
this one, each column header already applies to the cells below it.

## Notes under a table

A note under a table that starts with a symbol (*, †) used in the table's cells is tagged as a note, and the cell is
read with it ("500 (footnote asterisk)"). See [Footnotes](footnotes.md).
