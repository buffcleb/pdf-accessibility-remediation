# Tables

[The tag manager](README.md) › Tables

A screen reader announces a table's size, then reads each cell with its headers, so a number makes sense on its own.
In the tag tree a table holds rows (TR), and each row holds header cells (TH) and data cells (TD).

## The table editor

**Edit table…** in the menu of a table, or of anything in it (under **Lists and tables**), shows the table's cells in
a grid. Choose a cell, then:

- **Merge right** or **Merge down**: the cell next to it joins it, and the merged cell spans both (its content
  follows, in reading order). **Split** takes a spanning cell back to one place; the places it covered become empty
  cells.
- **Add row below** or **Add column right**: empty cells (a cell spanning across where they go grows instead).
- **Remove row** or **Remove column**: its content joins the row above (the column to its left; for the first row or
  column, the one after it), so nothing on the page is left untagged.
- **Make it a header** or **Make it a data cell**.

Above the grid, **Header rows** and **Header columns** make the first rows and columns headers at once (the corner,
where they meet, heads both ways), and **Caption** makes the paragraph just before or after the table its caption
(or takes it away: it becomes a paragraph again). **Apply** puts the whole table in the unsaved changes as one
change; choosing **Edit table…** again starts from it. When the headers need more than Scope to describe them (two
header rows, say), each cell is linked to its headers by name, as when the tool tagged it.

**Make a table…** (on one or more paragraphs or headings, under **Lists and tables**) is for a table the tool didn't
recognize: their pieces of text are placed in rows and columns by where they sit on the page, the first row as
headers, in the same grid to correct before **Apply**. A line drawn as one piece stays in one cell: if a whole row
landed in one cell, split the tag at the gaps first ([Merging, splitting and deleting](merge-split-delete.md)).

A table drawn as a picture (a grid of ruled lines, often a fill-in form's table) may have been tagged as a **Figure**.
**Make a table…** works on the figure too: its words become the cells and the drawn rows give the grid's rows. The
ruled lines that have no place of their own go in the first cell, where screen readers skip them. The figure's alt text
goes, since it described a picture. Check the rows against the page, then **Apply**.

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
