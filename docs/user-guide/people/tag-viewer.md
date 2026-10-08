# Checking and fixing tags

[Guide for people submitting documents](README.md) › Checking and fixing tags

The tag viewer shows two versions of a document side by side, normally your original on the left and the
remediated file on the right, with the tags drawn over the pages. You can check every tag against the page and
fix what's wrong. Open it with **Open in tag viewer** on the document's page, or **Show** on an item in the
review list.

## Looking around

- **Show:** flagged tags (the default), all tags, the reading order (numbered), or no overlay.
- **Hover over or focus a tag** for its details, including what a screen reader will read. Clicking blank page
  hides the details.
- **Zoom:** 50% to 300%, or fit the width. **Scroll together** keeps both sides in step.
- **Version pickers** above each side choose what's shown: the original, the remediated file, a saved version,
  or **Tag tree**.
- **Tag tree:** pick *Tag tree* on one side to see the other side's tags as a tree. On the left it shows the
  right-hand version's tree; clicking a tag jumps to it and opens it for editing. The tree follows as you scroll.
  **Expand all** and **Collapse all** open or close every branch.

## Fixing tags

You can edit your own documents (and group admins can edit their group's). Select a tag on the right-hand page,
or in the tag tree; the editing panel on the right shows what can be changed:

- **Tag type:** for example paragraph to heading, or heading level. Only changes that make a valid structure are
  offered. Turning a list into paragraphs takes it apart into one tag per item; turning text into a list wraps it
  properly.
- **Alt text** for figures, including marking a figure as decorative (no description needed).
- **Read-aloud text,** when the drawn text reads wrongly, as in math ("x squared" rather than "x 2").
- **Language,** for a passage in another language.
- **Table header cells.**
- **Reading order:** drag tags, or use the arrows, to put them in the order they should be read.
- **Merge** neighboring tags into one (useful for a formula broken into pieces), or **split** a tag at one of its
  pieces, or anywhere in its text (click where the new tag should start).

### Saving

- Changes collect under **Unsaved changes**, where you can undo them one by one.
- **Save** creates a new version, "Human reviewed 1", "Human reviewed 2" and so on. **The original results are
  never changed.**
- Each save is checked like the original: tag integrity, the accessibility check, and that the pages still look
  identical.
- You can ask for an AI review of the saved version: none, flagged figures plus a sample, or every figure. Later,
  **Run the AI review** or **Review every figure** on a version does the same.
- The new version appears under **Versions** on the document's page, with its own downloads.

If you try to leave with unsaved changes, the viewer asks first: **Stay on this page** or **Leave without
saving**.

## Reporting a problem from the viewer

**Report a problem here** opens the help form about the selected tag, with the page and tag filled in. See
[Getting help](getting-help.md).

## Tips for checking

- **Figures:** read the alt text against the image. Does it say what the image is *for*? A chart's description
  should give the trend or the key values, not just "a bar chart".
- **Headings:** do they form a sensible outline? A heading should introduce a section, not just be bold text.
- **Reading order:** switch **Show** to *reading order* on multi-column pages, pages with sidebars and pages with
  figures, and check the numbers run the way a person would read.
- **Math and notation:** read the read-aloud text of formulas marked as written by AI; the AI can confuse similar
  symbols.
