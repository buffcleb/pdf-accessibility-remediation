# Chemistry, medical and engineering notation on real documents (Local AI)

**Date:** 11 October 2026 · **Code:** main at `63076fe` · **Issue:** #20, part 1 · **Documents:** 14 real
documents, 12 to 348 pages (long ones cut to their densest pages)

*Edited 13 October:* examples that quoted the documents, or the AI's reading of them, were replaced with
descriptions. The documents are other people's and are not reproduced here; the counts and findings are unchanged.

## Question

The AI fallback for specialist notation had been tested on a real document only for math. Chemistry, dosages
and engineering notation were covered by made-up examples. On real material:

- Are the right regions sent to the AI (false alarms, misses)?
- Does the consistency check reject the right readings?
- Are the readings good enough to hear?

## Setup

- **Documents** (kept locally in `samples/regression/`, not in the repository):

  | Kind | Documents |
  |---|---|
  | Chemistry | an admission test (13 pages), a lab manual (10 of 79), reaction-scheme lecture notes (29), a PhD thesis (11 of 185), a journal article (7), a 1996 report (20), a prize committee's background paper (22) |
  | Medical | three dosage-calculation worksheets (3, 9 and 24 pages) |
  | Engineering | two datasheets (23 and 83 pages) |
  | Controls | two physics problem sets with ordinary math (2 and 3 pages) |

- **AI:** Local AI only, writer Qwen3-VL 8B. The review model was switched off, so nothing here was scored by
  a second model. No paid option was run.
- **Processing:** the engine called directly on each document, with default settings (`AI_NOTATION_MAX` 100).

## Method

1. Each document was processed once.
2. Every reading was printed beside the text of the same region of the page, and read by hand: all of them for
   the short documents, the first 15 to 40 for the long ones.
3. Regions that were not sent were checked by counting the notation in each document's text.

The results were read without the knowledge of a chemist, pharmacist or engineer: "wrong" below means the
reading doesn't say what the page shows, which needs no specialist to see.

## Results

| Document | Regions sent | Readings used | Rejected | Time |
|---|---|---|---|---|
| Admission test (chemistry) | 29 | 28 | 1 | 78 s |
| Lab manual, 10 pages | 19 | 15 | 4 | 57 s |
| Reaction-scheme notes | 100 of 139 | 91 | 9 | 293 s |
| Thesis, 11 pages | 33 | 29 | 4 | 87 s |
| Journal article | 1 | 1 | 0 | 27 s |
| 1996 report | 1 | 1 | 0 | 180 s |
| Prize background paper | 2 | 2 | 0 | 49 s |
| Dosage worksheet (3 pages) | 2 | 2 | 0 | 28 s |
| Dosage worksheet (9 pages) | 14 | 14 | 0 | 69 s |
| Dosage packet (24 pages) | 34 | 34 | 0 | 101 s |
| Datasheet (23 pages) | 7 | 6 | 1 | 146 s |
| Datasheet (83 pages) | 0 | 0 | 0 | 317 s |
| Problem sets (2) | 0 | 0 | 0 | 4 s, 15 s |
| **All** | **242** | **223** | **19** | |

No AI call failed. By kind, as the detector labeled them: 160 chemistry, 50 medical, 5 math, 4 engineering, 4
other. Times include describing pictures, which is most of the time on the datasheets and the 1996 report.

### Detection

- **Chemistry is found reliably** where formulas carry subscripts: 29 regions on the admission test, 139 in
  the lecture notes.
- **False alarms: signal names read as formulas.** On the 23-page datasheet, the names of a supply and of a
  gate signal (letters that happen to be element symbols, with a subscript) were labeled chemistry. The
  readings were fine; the label on the report is wrong.
- **Sent for one unreadable character.** Bulleted paragraphs whose bullet has no Unicode value, and an address
  line with one such symbol, were sent whole. The AI returned the same words, so nothing was lost, and nothing
  gained for a call each.
- **Whole paragraphs sent for one formula.** A paragraph mentioning a formula once is sent complete (up to 400
  characters). Most rejections come from these: the AI reads only the formula, and the check rightly refuses
  ("236 of 240 characters on the page are missing").
- **Not sent, correctly:** the 83-page datasheet's 146 unit symbols (°, ±, µ, Ω) sit in table cells and
  sentences that the rules already read, and the problem sets' math is ordinary.
- **Not sent, and arguably missed:**
  - a conjugate written with a spacing bar before its letter
  - dimension tolerances on package drawings, which reach the AI only when a character is unreadable
  - chemistry drawn as pictures (most of the prize paper and the lecture notes' structures). These are
    figures, described by the picture route, not by this one.

### The consistency check

19 of 242 rejected (8%). Those read by hand were right to reject: the AI had read part of the region, or added
characters. But the check compares the AI's **transcription** with the page, and nothing compares what will be
**spoken** with either. Readings that passed and are wrong:

| On the page | Spoken | Problem |
|---|---|---|
| two groups at the end of a drawn structure | the same letters, with one more C | a carbon added |
| a scheme of two steps, one arrow after the other | two separate reactions | the scheme's order is lost |
| a tolerance: two decimals with a dash between | "minus" for the dash | a range read as a subtraction |
| a temperature range between two negative numbers | "minus" three times | the same |
| a salt with a group in parentheses and a count after it | letters and digits in a row | the parentheses, which carry the meaning, are dropped |

*Corrected 12 October:* two rows were removed from this table. "Answer d) is gone" and an invented "divided by"
were artifacts of how the readings were compared with the page (against the text of the region's box, which
took in a neighboring block), not errors in the readings: the block held answer e) alone, and the layout rules
had already read the stacked inch and millimeter dimensions as a fraction, which the AI followed. That second
one is the rules' mistake, not the AI's.

Confidence is no guide: 206 of 223 readings report 1.0, including nearly every one above.

### Reading quality

- **Dosages: faithful, and uneven.** Numbers and drug names were kept in all 50. Units are sometimes expanded
  ("micrograms", "milligrams per ... milliliters") and sometimes left ("mg per ml", "mcg/min"), within one
  document. Prescription shorthand is left as written: how often, by which route, drops per minute, the name
  of an infusion fluid. A screen reader will spell these or guess.
- **Chemistry: usable, and inconsistent.** The same subscript is spoken four ways across one document (as a
  digit, as a word, with "sub", or left unspaced for the screen reader), and a reaction arrow as "arrow" or
  "yields". Element symbols are sometimes spaced into letters, which reads chromium as "C, R". Charges are
  read well ("superscript 2 plus", "three minus").
- **The picture helps.** The AI restored characters the text had lost: a missing superscript minus, and three
  Greek letters in a formula whose symbols had no Unicode value. In the 1996 report, whose old file has a zero
  typed for the letter O and a lower-case L for the Roman numeral I, it read what was meant.
- **Semicolons between every word** in one reading of a sentence that begins with a formula, and a list
  marker dropped from it.

## Second batch (11 October, later the same day)

Five more documents, chosen for the gaps above, on the same model and settings:

| Document | Regions sent | Readings used | Rejected | Time |
|---|---|---|---|---|
| Signals textbook chapter (27 pages, engineering equations) | 76 | 39 | 37 | 134 s |
| Electrochemistry practice problems (13 pages) | 54 | 31 | 23 | 110 s |
| Clinical guideline, 11 of 332 pages | 7 | 7 | 0 | 132 s |
| Scanned homework of equations (6 pages, no text layer) | 5 | 2 | 3 | 172 s |
| Equilibrium worksheet (3 pages) | 3 | 2 | 1 | 34 s |

- **A unit changed.** In the guideline, read from a scan, a range in "mcg/mL" was spoken as "meg per
  milliliter". One letter's difference is inside the consistency check's tolerance. In a dosing document this is
  the most serious error found.
- **A digit changed.** On the scanned homework an angle of three digits was spoken with two of them swapped.
- **Engineering math is rejected about half the time** (37 of 76, 23 of 54, against 8% before). Displayed
  equations are extracted as fragments, with the summation sign as a letter and limits out of order, so a reading
  of the whole equation doesn't match the fragment it is checked against.
- **Capital shorthand is neither found nor expanded.** The guideline writes BID, TID and PRN without dots: its
  densest 11 pages sent 7 regions, and those that were read leave the shorthand as letters.
- **Scanned equations are barely found**: 5 regions in 6 pages of them, since detection reads the OCR text.
- **Reactions written as running text are under-found**: 3 regions on a worksheet that is reactions throughout.
- Ranges read as subtraction again, and typeset limits read literally (the angle brackets around a limit
  spoken as "less than" and "greater than").

Adding these documents to the regression set also found two more engine faults, fixed in #170 and #171: a tagged
document whose own tags claim content twice failed outright, and Acrobat's invisible text-recognition font
failed the missing-glyph rule.

## What changed because of it

Five engine faults turned up while the documents were being added to the regression set, none about notation;
all are fixed (#159 to #163): a figure limit of 300, embedded files missing a name entry, metadata in an early
form of XMP, links written straight into a page's annotation list, and pictures found only on the second look
at a flattened page.

The recommendations below became issues #172 to #176; the first, #172, adds a check on what is spoken. Recommended, in order of value:

1. **Check what will be spoken, not only the transcription.** Apply the same character comparison to the
   spoken text after putting spoken numbers and names back ("three" to 3, "plus" to +), or require that every
   list marker and number in the region appears in it. This catches the added carbon, and in the second batch the changed unit and
   the changed digit.
2. **Send the formula, not the paragraph.** When a prose block is flagged for a formula inside it, read only
   that span and leave the sentence to the screen reader. Fewer rejections, shorter calls, and less for the AI
   to reword.
3. **One style, set by the prompt.** Chemistry: element symbols kept together, "sub" for counts, parentheses
   spoken, "yields" for a reaction arrow. Dosages: units always expanded, and a short table of prescription
   shorthand ("q 6 h" every 6 hours, "p.o." by mouth, "b.i.d." twice a day, "gtt" drops). Ranges between
   numbers read as "to".
4. **Don't trust the reported confidence.** Leave "check it" on every reading, as now.
5. **Detector:** don't call a subscripted name chemistry when every "element" is one of V, I, C in a run of
   capitals with no lower-case letter; don't send a block whose only unreadable character is a leading
   bullet.
6. **The region limit (issue #20, part 2).** One document of fourteen passed it (139 regions), and its last
   pages kept the rules' readings. On Local AI the cost of a region is about 2 seconds and nothing else, so the
   limit could follow the AI option: higher on Local, as now on paid options. Ordering by need matters less
   than expected: regions with unreadable characters were 11 of the 223 read here.

## Limits

- One model (the 8B local one), one run each, no review model. The earlier report found the same model
  "rarely wrong, but often imprecise" on math; that holds here.
- Read by hand, without specialist knowledge, and only a sample of the two longest sets.
- The long documents were cut to their densest pages, so counts per document are not counts for the whole
  document.
- Paid options were not run. Whether a larger model fixes the style and the invented words is not known.

## Reproducing it

The documents are in `samples/regression/full/` and `quick/` on the test machine, listed in `NOTES.md` there.
Each was run through `run_remediation` with the Local AI provider in a throwaway worker container, with
`VERIFY_AI_ENABLED=false`. The raw results (the engine's result for each document, with every reading) were
kept outside the repository for the session and are not archived: rerun to compare.
