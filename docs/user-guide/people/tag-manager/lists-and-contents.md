# Lists and tables of contents

[The tag manager](README.md) › Lists and tables of contents

A list tagged as a list tells a screen reader how many items there are and where it ends. These tools are under
**Lists and tables** in the tag menu.

## Building a list from an outline

**Build a list from the lines…** is the easiest way to turn an outline the tool read as one paragraph into a real
list. It opens a window with each line of the selected text as a row, already set in by where it sits on the page:
numbered or bulleted lines start rows, and a wrapped line stays with the row above.

![The Build a list window for a paragraph of three numbered lines: "1. Replace the two oldest incubators in the
teaching labs", "2. Field equipment for the river study" and "3. Graduate travel to the spring conference". Each row
has left and right arrows and a choice set to List item, and the second and third have "Join with the row above".
Under them: "This makes 3 list items. Nothing is in the file until you save." Buttons: Apply and
Cancel.](../../../images/tag-manager/list-builder.png)

- Move a row in or out a level with its arrows (or **Alt+Right** and **Alt+Left**).
- Set a row that isn't part of the list to **Heading** or **Paragraph** (a title line above the points, say).
- Join or separate rows where the guess was wrong.
- **Apply** makes it one unsaved change. The tag tree and the page show the heading, list and nested items straight
  away, as they will be saved (dashed outlines on the page); they become tags you can change one by one after
  saving.

It works on one paragraph or several selected ones, but not on text that has read-aloud or alt text standing for all
of it.

## Making one list from several tags

**Make one list from the N selected tags** turns each selected paragraph or heading into one item of a single list, in
reading order. A list among the selected tags gives its items, so this also joins two lists (**Join the N selected
lists into one** when they're all lists). (Choosing **List** under **Change type** is different: it makes a separate
one-item list of each tag.) Figures and formulas can't become list items; they would lose their description.

## Nesting items

**Indent the item** puts a list item one level deeper, under the item before it, which is how an outline's sub-points
are made: make one list of all the lines, then select the sub-points and indent them. **Outdent the item** takes it
back out a level; the items after it in the nested list stay under it, so nothing is read in a different order.

Both work on items of a list you've just made, before saving, and on several items at once. On the page, a list
item's text stands for the item. Until you save, the tree marks these tags "list item" and "list level 2" rather than
redrawing them as a list.

## Making a table of contents

**Make it a table of contents** turns a list into a table of contents, which is how screen readers and checkers
recognize one: each item becomes an entry, its text a reference (a link in it stays a link), and a nested list becomes
a nested part of the table of contents. Right-click the list or any text in it; the whole list is converted, nested
lists included.

![The tag tree after Make it a table of contents, shown on a short three-item list: the list is now a TOC, marked
"table of contents", with three TOCI entries, each holding a Reference with the item's
text.](../../../images/tag-manager/toc.png)

- Set the levels first: once it's a table of contents, indenting no longer applies.
- If the contents page was read as paragraphs, build it into a list first (above), save, then convert it.
- Until you save, the tree shows the new types, and choosing **Keep it a list** undoes it.

## Where each entry goes

**New 25 October 2026** (#631). PDF/UA-2 asks each table of contents entry to name the heading it goes to. In a file
delivered as PDF/UA-2 (or one that came as PDF 2.0), the tool names it from the entry's own link. An entry with no
link is pointed at the one heading with the same words ("2.1 Getting started ..... 14" goes to "2.1 Getting started"),
and flagged **contents entry pointed at a heading by its text** for you to check. An entry no heading matches, or that
two headings match, is flagged **contents entry goes nowhere**.

To set or change where an entry goes, right-click it and choose **Goes to → "…"**. The headings with most words in
common with the entry come first. **Goes to no heading** takes the target away. Once you've chosen, the flag on that
entry goes.
