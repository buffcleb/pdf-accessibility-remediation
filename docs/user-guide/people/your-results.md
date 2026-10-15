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
stays but its files are removed. You can have them removed sooner: see [When you're done with a
document](#when-youre-done-with-a-document).

## The document page

Click a document's name to open its page.

![A document's page for Campus_Parking_Guide_2026.pdf: Passes PDF/UA-1, failed checks 111 to 0, Download Remediated PDF and Open in tag manager; notes about the two files and the external AI; three tiles (Fixed 111, To review 2, Still failing 0); the Needs review tab with two items, a passage in French and a link described only by its URL, each with Show; and on the side, the downloads and Ask for help.](../../images/document-page.png)

### At the top

- The name, page count and when it was submitted.
- **One verdict**, for example **✓ Passes PDF/UA-1**, with "Failed checks: 120 → 0".
- **Download** for the main file and **Open in tag manager**.
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
  Each version saved from the tag manager (Human reviewed 1, 2, …) has a tab of its own there too, with the rules it
  still fails. The "failed checks" count beside a version, in the tag manager, Versions and Downloads, links to it.
- **Versions:** your saved edits and corrected files, when there are any.

![The Accessibility check tab of the same document: the PDF/UA-1 check by veraPDF on the result (After chosen, beside Before and Visually exact file), marked Compliant, with 106 rules passed, 0 failed, 3,376 checks passed and 0 failed, and Download XML.](../../images/document-checks.png)

The chosen tab and section are in the page's address, so a link opens on them.

### On the side

- **Downloads:** each file with a line on when to use it, the **Report (PDF)**, your original, and the technical
  files (folded away).
  Once the document has a **human-reviewed** version, that file comes first, marked out: the newest one,
  with a list to pick an earlier one. Every version stays under the **Versions** tab.
  Sheet music also has **Music: the whole piece**: the piece as one MusicXML file, the one to open first.
  A document with music notation, where the music reading service is on, also has **music files**: a zip
  with one MusicXML file per music example, as read by software. Open them in notation software (MuseScore
  is free) to see and play each example: the quickest way to check a reading against the page, and what a
  musician who is blind would use in place of the picture.
- **Ask for help** ([how](getting-help.md)).

**Hide downloads and help** folds them into a slim menu so the tabs get the whole width; the page remembers your
choice.

## When you're done with a document

Once you've downloaded what you need, you can have a document's files removed early instead of waiting for the end
of the retention period. It keeps your list tidy and keeps fewer copies of your documents around.

- **One document:** on its page, under **Downloads**, choose **I'm done with it: remove the files**.
- **Several:** in **Your documents**, tick the boxes next to them (or the box in the heading for every one shown),
  then **I'm done with these: remove the files**. Documents still waiting or in progress, already removed, or with an
  open help request can't be ticked.

The files aren't removed at once: there's a short hold (24 hours by default, set by your administrators) so a
mistake can be undone. Until then the document's page says **Marked done** and when the files go, with **Keep the
files** to undo it; the list shows "Files go …" under the name. After the hold, the document stays in your list
with its results, but its files, report and versions are gone, as at the end of the retention period. Pages you
used aren't given back.

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
- **Show** opens the item in the tag manager, where you can fix it. **Back** there returns to the same item.

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

![The first page of the accessibility report PDF for the parking guide: a summary saying the remediated document passes PDF/UA-1, with failed checks going from 111 to 0, a list of what was done (bookmarks, tags, footnotes, alt text, tables, reading order), and a What changed table of headings, figures, tables, language and title before and after.](../../images/report-pdf.png)

The technical files (Markdown and JSON reports, veraPDF's XML) are under **Technical files**, for tools and
support. Documents processed before the PDF report existed have the Markdown report instead.

## Scanned documents

Scanned pages are pictures of paper. The service reads their text (OCR) and adds it as an invisible layer, so
it can be read aloud, searched and copied; the page looks the same.

- Where the OCR wasn't sure of a word, the AI corrects it from the image, and lines the OCR couldn't read at all
  are transcribed. Each change is flagged in the review list. Check transcriptions of very poor or handwritten
  lines in particular.
- Charts, photos and diagrams printed on the scan are found and tagged as figures with alt text.
- A scanned form with values typed onto it is read too: the printed form by OCR, the typed values as they are.
- Equations on a scan mostly come out of OCR as a jumble of letters and signs. Where they do, the AI reads them
  from the image (asked once per such page where the equations are), and its reading is what a screen reader says.
  Each one is flagged in the review list; check them, as with any AI reading. A scanned page of ordinary text costs
  no extra AI.
- Tables on scanned pages are tagged as text, not as tables, for now.
