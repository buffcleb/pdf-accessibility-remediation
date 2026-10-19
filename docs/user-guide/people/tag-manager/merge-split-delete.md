# Merging, splitting and deleting

[The tag manager](README.md) › Merging, splitting and deleting

These are under **Merge and split** in the tag menu, and in the editing panel.

## Merging

**Merge** joins neighboring tags into one: useful for a formula, or a sentence, broken into pieces. Select the tags
(Ctrl-click or Shift-click), then **Merge the N selected tags into one**: they're joined in reading order, and the
merged tag keeps the type of the first one; change it afterwards if needed.

## Splitting

**Split…** cuts a tag in two, at one of its pieces, or anywhere in its text: click where the new tag should start,
then **Split at the cursor**. On the page, right-clicking over a later part of a tag that has several parts offers
**Split here**.

![The Split this paragraph window: the paragraph's text in a box, "Click where the new tag should start", a note that
the text is shown in the order it's drawn and that read-aloud text is removed from both parts, and the buttons Split
at the cursor and Cancel.](../../../images/tag-manager/split.png)

For a figure made of several parts, see [Splitting a figure](figures.md#splitting-a-figure).

## Deleting

**Remove or delete this tag…** asks what to do with what's inside it:

![The Delete this Section tag window for the section "Buying a permit". Two choices: "Remove only this tag. The 3 tags
directly inside it move up a level, into the Section" (chosen), and "Delete it and everything in it. The 9 tags inside
it are deleted too", followed by the text that would no longer be read by screen readers. Buttons: Cancel and Remove
the tag.](../../../images/tag-manager/delete.png)

- **Remove only this tag:** the tags inside it move up a level. Offered only where that leaves a valid structure (a
  list's items can't move out of their list, or a row's cells out of their row). Text the tag held directly stays
  when the tag above can hold text (a span inside a paragraph); otherwise it's no longer read, and the window shows
  which text.
- **Delete it and everything in it:** the tags inside go too, and all of its content becomes an artifact, which
  screen readers skip. The window shows the text that will stop being read.
- Links and form fields have to stay tagged. Deleting a tag with everything in it keeps the links and fields inside
  it: each moves up into the tag's place, with its own text. A tag that is itself a link or a field can't be deleted,
  nor can the document's top tag.

Like every change, nothing happens to the file until you save, and it can be undone under **Unsaved changes**.
