# Editing a tag

[The tag manager](README.md) › Editing a tag

Select a tag on the right-hand page, or in the tag tree, then change it in one of three places:

- **The tag menu:** right-click the tag (in the tree or on the page). From the keyboard, press **Shift+F10** (or
  the Menu key) on a tag, or use **Actions** at the top of the page.
- **Edit tag…** in the menu opens the tag in a window: what it says, its findings, and its type, alt text,
  read-aloud text and language. On a version you can't edit, it's **Tag details…** and shows the same, read-only.
- **The editing panel** beside the pages holds the same tools. It's hidden at first, to give the pages its width:
  **Panel** at the top shows it again, and it stays as you leave it.

Nothing is in the file until you save ([Saving](saving.md)).

## The tag menu

![The tag menu open on a paragraph in the tag tree. Under "This tag": Change type…, Split…, Reading order (with a
submenu arrow), Points at "1 The state fee is set each year by the", Edit tag…, Remove or delete this tag…, Read aloud
from here, Report a problem with this tag, and Isolate this tag.](../../../images/tag-manager/menu.png)

**Merge and split**, **Lists and tables**, **Reading order**, **Footnote** and **Mark as artifact** each open a submenu
beside it (point at it, click it, or press Right, Enter or Space; Left or Escape goes back). A kind with only one
choice, and the everyday actions (remove, read aloud, report, isolate), are on the top level itself. A choice that
can't be made stays in the menu, dimmed, with the reason under it.

With one tag selected, the menu also offers **Split here** (when you right-clicked on the page over a later part of a
tag that has several parts), reading it earlier or later on its page, removing the tag, reading aloud from it, and
undoing its unsaved changes.

**Mark as artifact** is for content that shouldn't be read at all, left tagged: a running header or footer, a page
number, a rule or ornament: choose **Decoration**, **Running page header**, **Running page footer** or **Page number**
(for a figure, **Decorative**). The tag and everything in it go, and its content is marked as the page furniture it
is, so screen readers skip it. It's offered for text, figures and the sections that hold
them, but not for a tag holding a link or form field, which has to stay readable.

**Right-click the page where there is no tag** for the area tools: see [Areas](areas.md).

## What can be changed

![The Edit tag window for a figure on page 2: Tag type set to Figure, the alt text "Bar chart showing permit sales by
lot: North (420), Library (310), Stadium (520), East (260), Medical (380). Stadium lot had the highest sales, East lot
the lowest.", an empty Language field, a
Decorative check box, and the buttons Add change, Delete this tag… and Close.](../../../images/tag-manager/edit-tag.png)

- **Tag type:** for example paragraph to heading, or a heading's level. Only changes that make a valid structure are
  offered (see below).
- **Alt text** for figures, including marking a figure as decorative. See [Figures](figures.md).
- **Read-aloud text,** when the drawn text reads wrongly, as in math ("x squared" rather than "x 2"), or letters drawn
  out of order. **Start from the drawn text** fills the box with what's on the page, to correct.
- **Two readings of a formula,** when they disagree. The AI reads each formula aloud and also transcribes it; the
  service then reads the transcription by rule (MathCAT) and compares the two. When they say different numbers or
  letters, the formula is flagged "two readings of a formula disagree", and Edit tag shows the formula drawn from
  the transcription, what differs ("numbers: the AI has 2, by rule 3"), and both readings. Pick the one that matches
  what's printed and it becomes the formula's read-aloud text; if neither does, write your own in the box. Choose
  **Add change** even when you keep the AI's reading: the choice settles the finding, and the service records which
  reading was right, to improve how it reads formulas. Where the service also reads formulas from their pictures
  (a separate reader, independent of the AI), its reading is offered too, with the formula as it read it.
- **Language,** for a passage in another language (fr-CA, es, de…). Leave it empty when it's the document's own.
- **Link description,** for a link's tag: what a screen reader says for the link. Say where it goes ("Annual
  report, PDF"), not "click here". It's written on the link itself too, where readers look for it, and can be changed
  but not left empty.
- **Field label,** for a form field's tag: what a screen reader announces for the field ("Last name", "Payment
  method: Card"). The service names fields from their printed labels, and asks the AI when nothing on the page
  labels one; the review lists any still called by an internal name ("Text1"), and the report says how many the AI
  named, to check.

Choose **Add change** to add what you changed to the unsaved changes.

## Changing a tag's type

**Change type…** opens a window listing the types, each with what else choosing it does or why it can't be chosen.
Escape or **Cancel** closes it without a change.

![The Change type window for a paragraph: Heading 1 to Heading 6, List (with the note "The text is wrapped in a list
with one item (L > LI > LBody)"), Caption, Figure and Formula, and a Cancel
button.](../../../images/tag-manager/change-type.png)

- Turning a list into paragraphs takes it apart into one tag per item; turning text into a list wraps it properly.
- A tag changed to **Note** (a footnote the tool didn't find) is given the ID a note needs; then
  [link its mark to it](footnotes.md).

## Several tags at once

Select several tags first to change them together:

- **Ctrl-click** (**Cmd-click** on a Mac) adds a tag or takes it out, and in the tag tree **Shift-click** takes every
  row from the selected tag to the one you click.
- From the keyboard in the tree, **Shift+Up** or **Shift+Down** extends the selection and **Ctrl+Space** adds or
  removes the row you're on.
- The panel shows the first tag and says how many are selected.

With several tags selected the menu can **change them all to one type** (five paragraphs into headings, say),
**merge them into one tag** in reading order, or [make one list](lists-and-contents.md) from them.

## The document's title and language

**Document…** at the top opens the whole document's **title** and **language**:

- The **title** is what a screen reader announces when the document opens, and what the title bar shows. Make it
  say what the document is ("Annual report 2026"), not a file name.
- The **language** is the one most of the document is in (en-US, fr, es…; the field suggests common ones). A passage
  in another language keeps its own, set on its tag above.

**Add change** puts them with the other unsaved changes, and they're saved the same way; the saved version lists
them among its changes ("document title set to …").

