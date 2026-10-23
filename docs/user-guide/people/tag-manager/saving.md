# Saving your changes

[The tag manager](README.md) › Saving your changes

Changes collect under **Unsaved changes (N)…** at the top of the page (and in the panel's tab), where you can undo
them one by one and save. In the tag tree, tags with unsaved changes are tinted and say what changed.

**Undo** and **Redo** at the top take back your changes in the order you made them, and bring them back:
**Ctrl+Z** and **Ctrl+Shift+Z** (or **Ctrl+Y**); on a Mac, **⌘Z** and **⇧⌘Z**. Each says what it undid. In a text
field the keys undo your typing instead, as usual. The history starts again when you save, switch versions or pick
up kept changes; the last 200 steps are kept.

![The Changes not saved yet window with one change: a list "made into a table of contents (page 1)", with Undo. Below:
an optional Note box, the choice "AI review of the saved version" (None now, chosen; Flagged figures plus a sample;
Every figure), the buttons Save as a new reviewed version and Discard all, and a note that saving re-checks the tags,
runs veraPDF and confirms the pages look the same.](../../../images/tag-manager/unsaved.png)

## Saving a version

- **Save** creates a new version, "Human reviewed 1", "Human reviewed 2" and so on. **The original results are never
  changed.**
- When the version you're editing is itself a human-reviewed one, you choose **where to save**: **into it** (the usual
  thing while a long review goes on: it keeps its name, and the list of versions stays short) or **as a new reviewed
  version**. A save into a version replaces its file only once the new one passes the checks. The file it had before is
  kept for 24 hours: until then, **Undo this save** under **Versions** on the document's page puts it back.
- Each save is checked like the original: tag integrity, the accessibility check, and that the pages still look
  identical.
- A new version opens as soon as its file and tags are saved, marked **checking…**, while the accessibility check
  and the page comparison finish: on a long document that's a minute or two sooner. Its result and review items
  appear when they're done. (A save into a version waits for the checks, as it replaces that version only then.)
- The new version appears under **Versions** on the document's page, with its own downloads.

## AI review

You can ask for an AI review of the saved version: none, flagged figures plus a sample, or every figure. Later, **Run
the AI review** or **Review every figure** on a version does the same. **AI review…** beside **Unsaved changes**, on a
human-reviewed version, shows the review's state and runs it again.

## Unsaved changes are kept for you

A moment after each change, your unsaved changes are stored with the document, so you can close the page, or come back
on another computer days later, and carry on where you stopped: the tag manager puts them back and says when they were
last kept. Nothing is in the file until you save. Saving a version, or **Discard all**, clears them. If the tag manager
couldn't reach the server, it says so and asks before you leave: **Stay on this page** or **Leave without saving**.

## When someone else has unsaved changes

A document's owner and their group's admins can all edit it. When someone else has unsaved changes on the same
document, the tag manager tells you who and when, and offers three things: **View read-only**, **Start my own**, or
**Start from their changes** (a copy; theirs stay as they are). Each person's unsaved changes are their own, and two
sets saved separately make two versions, so agree who carries on.
