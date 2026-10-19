# Reading order

[The tag manager](README.md) › Reading order

A screen reader reads the tags in order, whatever their place on the page. Check the order on multi-column pages,
pages with sidebars and pages with figures.

## Checking it

Set **Show** to *Reading order*: each tag with content is numbered on the page. Choose **Reading order** at the top of
the tag tree's pane to list the same tags, page by page, in the order they're read.

![The parking guide with Show set to Reading order. The list on the left and the numbers on the page agree: 1
"Northfield University", 2 the heading "Campus Parking Guide 2026-27", 3 the introduction paragraph, then the next
heading, the list items and the table's cells in order, row by row.](../../../images/tag-manager/reading-order.png)

Look for numbers that jump: a paragraph read before the heading above it, a sidebar or caption read in the middle of
a sentence, a second column read before the first ends. Hearing the page also shows these problems: see [Hearing it
read aloud](read-aloud.md).

Pages mostly in Arabic or Hebrew are read from the right: columns and items side by side start on the right. The
review list names these pages ("right-to-left reading order") so you can check them.

## Moving tags

In the **tag tree** (of a version you can edit), drag a tag to another place, or move it with **Alt+↑** and
**Alt+↓**. To put it inside a section, drop it next to one of that section's tags. **Reading order** in the tag menu
also reads a tag earlier or later on its page.

- The **Reading order** list works the same way (drag, or **Alt+↑** and **Alt+↓**, with the same rules), and it's
  the same change everywhere: the tree, the list and the numbers on the page all follow at once.
- A drop that would make the tags invalid is refused, with the reason: a paragraph can't go between a list's items
  or a table's rows, a table cell can only move within its row (another row would make the rows uneven), and a list
  item stays in a list.
- A moved tag shows an **undo** button (↩) in the tree and in the list; **Alt+Delete** does the same from the
  keyboard. It takes back that tag's moves only. **Undo** (Ctrl+Z) takes back the last change, whatever it was, and
  other changes can be undone one by one under **Unsaved changes** ([Saving](saving.md)).
