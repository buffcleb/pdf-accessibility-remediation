# AI-assisted OCR correction on damaged scans

**Date:** 7 October 2026 · **Code:** E2 (issue #18), branch `feat/ocr-ai-correction` · **AI:** Local AI only
(Qwen3-VL 8B on our own hardware, no cost)

## Question

On scanned pages, the OCR text is the document's content: what screen readers read and search finds. Can
the AI improve it where Tesseract is unsure, without inventing text?

## Setup

- **Ground truth:** two real text PDFs with known text:
  - the first page of a course syllabus (`samples/syllabus_NOTaccessible.pdf`)
  - the first page of a published HR policy
- **Simulated scans:** each page was rendered to an image and damaged, then saved as an image-only PDF:

  | Level | Resolution | Blur | Speckle noise | JPEG quality |
  |---|---|---|---|---|
  | clean | 300 dpi | none | none | 95 |
  | light (a typical poor scan) | 150 dpi | 0.8 px | 0.2% | 40 |
  | heavy (barely legible) | 110 dpi | 1.1 px | 0.6% | 25 |

- **Measure:** word accuracy. The share of the original page's words that appear, in order, in the OCR
  text (difflib matching over word sequences).
- Run in the engine image (Tesseract 5.3), with the AI through the local gateway.

## Results

### Which OCR

Word accuracy and text-layer alignment (the share of OCR lines whose invisible text overlaps the printed line
by more than half), over **three noise patterns** per damage level. Where the speckles fall moves the results
by a few points, so ranges are shown.

| Scan | PyMuPDF built-in OCR (used before) | Tesseract command line |
|---|---|---|
| Syllabus, clean | 0.815 | **0.935** (all 55 lines aligned) |
| Policy, clean | **0.893** | 0.888 |
| Syllabus, light | 0.69–0.76, 36–40% aligned | **0.82–0.85, 61–67% aligned** |
| Policy, light | 0.76–0.78, 34–45% aligned | **0.79–0.80, 51–55% aligned** |
| Syllabus, heavy | **0.31–0.45**, 23–42% aligned | 0.13–0.21, 38–58% aligned |
| Policy, heavy | **0.33–0.40**, 18–26% aligned | 0.28–0.33, 44–53% aligned |

- **The command line is consistently better on typical poor scans,** in both accuracy and alignment, and it
  reports a confidence per word, which the AI step needs.
- **It's consistently worse on barely legible scans,** although its text lines up better there. The AI step
  recovers part of that gap (below).
- It's now the OCR source whenever it's installed. `OCR_ENGINE=pymupdf` switches back, for collections of
  mostly very poor scans (without AI correction).
- Table rows are split into cells: on the clean syllabus, alignment went from 23 of 30 lines (rows written as
  one squeezed line) to 55 of 55.

### With AI correction (Local AI)

| Scan | Tesseract alone | + AI | Lines sent / corrected / transcribed / rejected |
|---|---|---|---|
| Syllabus, light | 0.840 | **0.860** | 23 / 4 (6 words) / 5 / 12 |
| Syllabus, heavy | 0.210 | **0.345** | 18 / 3 (8 words) / 9 / 5 |
| Policy, light | 0.794 | **0.828** | 30 / 12 (16 words) / 2 / 14 |
| Policy, heavy | 0.279 | **0.359** | 32 / 4 (8 words) / 10 / 18 |

(These four runs use noise pattern 1.) Compared with the OCR used before E2, a typical poor scan of the
syllabus goes from **0.755 to 0.860**, and the policy from **0.762 to 0.828**. On the barely legible syllabus,
the AI brings the command line's 0.210 up to 0.345, still below PyMuPDF's 0.380 for that pattern. On the
policy, 0.279 becomes 0.359, above PyMuPDF's 0.333.

**Corrections the AI made:**
- "suddents" → "students"
- "basit" → "basic"
- "Depertinens:" → "Department:"
- "— Ifan ernployes" → "If an employee"
- "designieé" → "designee"
- "aid Leeve Marival" → "and Leave Manual"
- "he@[college].edu" → "hr@[college].edu"

**Transcriptions of lines Tesseract couldn't read:**
- "Atcessi wh pUnmiversity" → "Accessible University" (correct)
- "' Course Synenus" → "Course Syllabus" (correct)
- "wrth" → the college's name (probably read from the header above, not the line itself)

**Rejected by the checks (Tesseract's text kept):**
- The AI dropped a real word.
- It added words ("suddents" → "many students").
- It changed a word Tesseract was sure of.
- A transcription didn't fit the line it came from.

## What testing changed

The first version of E2 barely helped (syllabus, light: 0.755 → 0.775). These changes brought it to the
results above:

1. **One OCR reading instead of two.** The first version took the text from PyMuPDF and the confidence from a
   separate Tesseract pass. On damaged scans the two disagreed, so 56 of 69 lines had no usable confidence
   and were never considered. Text and confidence now come from the same Tesseract reading.
2. **Splits and merges allowed.** Tesseract often joins words ("Toprovide", "Chapter1"). The first check
   required the same number of words, so it rejected the AI's correct splits. Now confident words may only
   be re-spaced, and uncertain words may be split or merged if the result looks like the original.
3. **Punctuation kept.** The AI tends to drop printed punctuation ("University." → "University"); the
   original punctuation is now put back.
4. **Junk skipped:** table borders ("|") and dotted or underlined form lines ("...", "___") aren't sent to the
   AI, and "[illegible]" alone is never accepted.
5. **Table rows split into cells** in the text layer (alignment above).

## Conclusions

- **A clear gain on poor scans.** On a typical poor scan, the new OCR plus AI correction reads 7–10% more of
  the page's words correctly than before E2, and confident words are never touched.
- **Transcription is where mistakes can happen.** On barely legible lines the AI sometimes reads a
  neighboring line. Every transcription is flagged for review, and those flags are the ones most worth
  checking.
- **Even the best result isn't good enough unchecked.** At 0.86 word accuracy, about one word in seven on a
  poor scan is still wrong or missing. Scanning at a decent resolution matters more than any correction.

## Limits

- **Barely legible scans:** the new OCR reads them worse than before, and the AI only partly makes up for it.
  Better image preparation before OCR (upscaling, binarization, despeckling) or choosing the engine per page
  could fix that; it's a follow-up.
- **Simulated damage on two pages.** Real scans (skew, shadows, faded toner, handwriting) behave
  differently, and no real handwriting was tested.
- **Local AI only:** Cloud and Commercial weren't run, since paid runs happen only with the service owner's
  go-ahead. A stronger model should transcribe barely legible lines better.
- **The measure ignores word positions:** it counts words in order, not whether each sits exactly over its
  printed word. Line-level alignment was measured separately.

## Next tests to run

- **Scanned handwriting, including poor cursive.** Real scans of handwritten pages: forms filled in by hand,
  annotated worksheets, handwritten notes and letters in joined-up (cursive) writing of varying neatness,
  scanned at ordinary and poor quality.
  - Measure how often lines go to the AI for transcription, how accurate the transcriptions are against a
    hand-made transcript, how often the checks reject them, and whether the text layer still lines up.
  - Compare Local with Cloud and Commercial. Paid runs need the service owner's go-ahead.
  - This is where transcription matters most and is most likely to go wrong. The simulated scans above
    contain only printed text.
- **Real poor scans** (skewed, shadowed, faded), rather than simulated damage.
- **Image preparation before OCR** (upscaling, binarization, despeckling) on barely legible scans, to see
  whether it closes the gap with PyMuPDF's OCR.

## Reproducing it

`harness/ocr_eval.py` damages pages of a text PDF, runs the OCR and (optionally) the AI correction, and reports
word accuracy and text-layer alignment. Run it in the engine image, which has the Tesseract command line, with
the gateway reachable:

```bash
docker run --rm --network <compose-project>_default -e GATEWAY_API_KEY=… -e GATEWAY_BASE_URL=http://litellm:4000/v1 \
  -v "$PWD":/w -w /w <compose-project>-worker python -m harness.ocr_eval samples/syllabus_NOTaccessible.pdf \
  --pages 0 --level light --model local-vision
```

`--model none` measures Tesseract alone. Paid models (cloud-vision, commercial) cost money: run them only
when that's been agreed.
