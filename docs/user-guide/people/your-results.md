# Your results

[Guide for people submitting documents](README.md) › Your results

## Finding your documents

**Your documents**, at the bottom of **My documents**, lists everything you've submitted, newest first:

- **Search** by file name, and pick a **status** (waiting, in progress, done, failed and so on).
- **Filters** adds more: the result (passes, needs work, items to review, an open help request), processing
  option or AI level, remediated or checked only, and a date range. Filters in use show as chips you can remove.
- Click **Document**, **Submitted**, **Pages** or **Result** to sort. Show 25, 50, 100 or 200 at a time, with
  **Previous** and **Next**.
- The list updates by itself while documents are being processed. The view is kept in the page's address, so
  Back from a document, and a bookmark, return to it.

Files are kept for a set time (30 days by default), shown on each document; after that the document's record
stays but its files are removed.

## The document page

Click a document's name to open its page.

### At the top

- The name, page count and when it was submitted.
- **One verdict**, for example **✓ Passes PDF/UA-1**, with "Failed checks: 120 → 0".
- **Download** for the main file and **Open in tag viewer**.
- While it's being processed, a row of steps shows how far it's got: Waiting, Checking the original, Tagging,
  Alt text and tables, Checking the result.

### Notes

Short notes, one line each, for anything worth knowing, with **Why?** for the detail. For example: two files
(see below), tagged by Adobe, the AI used *General* instead of the kind you chose, no AI was needed, the kind
picked by *Let AI decide*, or that an outside service was used.

### Three tiles

**Checks fixed**, **items to review** and **rules still failing**. Each opens its tab.

### Tabs

- **Needs review:** what a person should check (below).
- **Report:** the remediation report, in sections (Summary, Changes, Still failing, For review, Details), with
  **Download the report**.
- **Accessibility check:** the PDF/UA-1 check rule by rule, before and after.
- **Versions:** your saved edits and corrected files, when there are any.

The chosen tab and section are in the page's address, so a link opens on them.

### On the side

- **Downloads:** each file with a line on when to use it, the **Report (PDF)**, your original, and the technical
  files (folded away).
- **Ask for help** ([how](getting-help.md)).

**Hide downloads and help** folds them into a slim menu so the tabs get the whole width; the page remembers your
choice.

## Which file to use

Most documents have **one remediated file**: use it.

Some can't pass without a visible change, usually because a font isn't embedded in the PDF or can't be read as
text. Then you get **two**:

| File | What it is | When to use it |
|---|---|---|
| **…compliant visual** | Looks exactly like your original, with all the tags. May still fail a few checks, usually about fonts | When the look must not change at all |
| **…compliant modified** | Passes more or all checks. Fonts may be swapped for look-alikes with the same spacing, and on rare pages the page is turned into an image with an invisible, tagged text layer | When passing matters more than exact rendering |

The report says exactly what changed in the modified file, and on which pages.

## The review list (Needs review)

The machine checks rules; it can't always tell whether something is *right*. The review list is what it wasn't
sure about:

| Severity | Meaning | What to do |
|---|---|---|
| 🔴 **High** | Likely to hide content from screen readers, or to say something wrong | Fix before publishing |
| 🟠 **Review** | Probably wrong; a person should look | Check, then accept or correct |
| 🔵 **Suggestion** | A possible improvement | Optional |

Each item gives the page, the tag, the problem and a suggested action. For alt text, the suggestion includes a
proposed rewrite.

- With more than a few items: buttons for All, High, Review and Suggestions (with counts), a search (or a page
  number), and 25 at a time.
- **Show** opens the item in the tag viewer, where you can fix it. **Back** there returns to the same item.

**Typical items:** alt text that may be inaccurate (charts, diagrams), a heading that looks like body text, a
paragraph that looks like a missed heading, link text that's just a web address, a passage in another
language, a reading of math or chemistry written by AI, text corrected or transcribed by AI on a scanned page.

**AI-written alt text is a draft.** The AI checker catches many mistakes but not all. Spot-check figures that
carry meaning: charts, diagrams and formulas.

## The report

The **Report (PDF)** is written for people, and is accessible itself (it passes PDF/UA-1): a summary, what
changed, the check rule by rule before and after, what still fails, the items for a person to check (up to 150;
the rest are in the portal) and details. It carries your service's name and color. The Report and Accessibility
check tabs download it too.

The technical files (Markdown and JSON reports, veraPDF's XML) are under **Technical files**, for tools and
support. Documents processed before the PDF report existed have the Markdown report instead.

## Scanned documents

Scanned pages are pictures of paper. The service reads their text (OCR) and adds it as an invisible layer, so
it can be read aloud, searched and copied; the page looks the same.

- Where the OCR wasn't sure of a word, the AI corrects it from the image, and lines the OCR couldn't read at all
  are transcribed. Each change is flagged in the review list. Check transcriptions of very poor or handwritten
  lines in particular.
- Charts, photos and diagrams printed on the scan are found and tagged as figures with alt text.
- Tables on scanned pages are tagged as text, not as tables, for now.
