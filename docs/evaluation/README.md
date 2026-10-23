# Evaluation

How the proof of concept was tested, what it achieved, and what it costs to run. Individual experiments are written
up in [test reports](test-reports/README.md), and reviews of the code in [reviews](#reports-and-reviews). Each report
is a record of its day; later changes are noted in it only as "fixed since".

**Updated 22 October 2026:** later changes to a report or review are now marked **Updated** with their date, at the
finding they update; on this page, every paragraph or row brought up to date carries the same marker.

## Contents

- [Test method](#test-method)
- [Results](#results)
- [What still needs a person](#what-still-needs-a-person)
- [A pass is not full accessibility](#a-pass-is-not-full-accessibility)
- [Known limitations](#known-limitations)
- [Comparing with another remediation service](#comparing-with-another-remediation-service)
- [Cost model](#cost-model)
- [Reports and reviews](#reports-and-reviews)

## Test method

Each document goes through the harness (`harness/run_harness.py`). For each output file it records:

- veraPDF PDF/UA-1 failed checks, before and after;
- the tag integrity check (a failure fails the job);
- pixel identity of the visually exact file against the original, page by page;
- the "Needs human review" items;
- time, AI calls, tokens and cost.

**Updated 22 October 2026:** the outputs are also checked in PAC 2026 (PDF/UA-1), by hand on a Windows machine, since
PAC found problems veraPDF passed ([test report](test-reports/2026-10-22-pac-regression-set.md)). The harness itself
still checks veraPDF only.

| Set | Files | Pages | What it covers |
|---|---|---|---|
| Synthetic (`harness/make_samples.py`) | 9 | about 40 | Untagged, partly tagged with broken tags, scanned, two-column, tables, a Quartz text export, unembedded fonts |
| Real documents | 13 | 431 | arXiv and journal papers (LaTeX), a dissertation, a thesis, a college accessibility plan (original and "compliant" versions), a catalog, a brochure, a policy, a math formula sheet, sheet music, image-heavy files |
| Further real documents | 30 | – | The above plus fillable forms, scans and more formula sheets, used for the classifier comparison and regression checks |
| **Updated 22 October 2026:** the regression set | 30 | 330 (quick set), 1,931 (full set) | The 30 documents above, shortened (quick set) and at full length (full set): reports, datasheets, textbooks, exams, forms, scans, music, chemistry and math. Checked with veraPDF and PAC |

The real documents aren't in the repository.

## Results

### Real documents (13 files, 431 pages; Qwen3-VL 8B writing, Gemma 4 26B reviewing, both local)

| Result | Count |
|---|---|
| Pass veraPDF PDF/UA-1 | **13 of 13** |
| Pass tag integrity | 13 of 13 |
| Visually exact file pixel-identical to the original | 13 of 13 |
| Needed a second, visually modified file to fully pass | 2 (an arXiv paper: 1 figure page flattened; a journal paper: invisible font-width corrections) |

| Document | Failed checks before → after |
|---|---|
| Journal paper (LaTeX) | 29,770 → 0 |
| arXiv paper (LaTeX) | 13,496 → 0 |
| College accessibility plan (original) | 5,703 → 0 |
| Journal paper | 984 → 0 |
| Thesis | 798 → 0 |
| College accessibility plan ("compliant" version) | 689 → 0 |
| Math formula sheet, dissertation, catalog, brochure, policy, sheet music, images | all → 0 |

Two documents labeled "COMPLIANT" (presumably from earlier remediation) still had 689 and 984 failed checks before
this engine processed them.

### PAC 2026 on the regression set (30 documents)

**Updated 22 October 2026:** checked through the deployed service, existing tags kept, Local AI, without full figure
review ([test report](test-reports/2026-10-22-pac-regression-set.md)):

| Route and set | First check | After the fixes |
|---|---|---|
| Engine, quick set (20 October) | 26 of 30 pass PAC | 30 of 30 (#483, #484, #486) |
| Engine, full set (21 October) | 28 of 30 pass PAC; both failures had passed veraPDF | 30 of 30 (#526, #528), re-checked 22 October |
| Adobe route, quick set (20 October) | 35,976 failures and 826 warnings, traced to Adobe's own tagging | The service's improved copy adds no PAC findings |

So the engine's output for all 30 documents now passes both veraPDF and PAC (PDF/UA-1). Not checked in PAC: PDF/UA-2,
"Ignore existing tags", the paid AI levels, full figure review, and the Adobe route on the full set.

### Synthetic set (9 files)

All 9 pass PDF/UA-1, including a file with deliberately broken existing tags: 1 tag was kept, 3 were repaired and 1
was replaced.

### Throughput

| Setup | Pages per minute (figure-heavy dissertation) |
|---|---|
| LocalAI on an RTX 3090 + RTX 4060 Ti, 1 worker | about 19 (109 pages in about 350 s) |
| Ollama on an Apple M1 Max, 1 worker | about 6 |

Plain-text pages are much faster and figure-heavy pages slower, because AI time scales with figures, not pages.
Workers scale horizontally.

### Specialist notation and OCR

- **Math-heavy documents:** on a 17-page research paper, a commercial model read 68% of formulas fully accurately and
  the local-class model 26% (nearly all the rest partly right), at $0.96 and $0.03 for the paper. All three AI levels
  took it from 13,496 failed checks to 0. ([Test report](test-reports/2026-10-07-notation-ai-tiers.md))
- **Damaged scans:** on a typical poor scan, the new OCR plus local AI correction reads 7–10% more words correctly
  (0.755 → 0.860 on a syllabus). ([Test report](test-reports/2026-10-07-ocr-ai-correction.md))

## What still needs a person

The engine reports these; it doesn't hide them:

- **Alt text for complex figures,** especially math: the reviewer flagged 76 of 106 checked alt texts in the
  dissertation and suggested rewrites.
- **Readings of math and notation by AI,** especially from smaller models.
- **Link text:** links described only by their address, and link tags in the original that aren't connected to their
  links.
- **Forms:** only partly handled.
- **Tables in scanned pages:** tagged as text.

## A pass is not full accessibility

veraPDF checks the machine-verifiable rules of PDF/UA-1. It can't tell whether alt text is accurate, whether headings
are the right ones, or whether the reading order makes sense. That's why the engine adds its own checks, an AI review
and a human review list. The same applies to any other remediation tool's output.

**Updated 22 October 2026:** the same holds for PAC, which adds checks of whether the structure makes sense (Parts
nested in Parts, content directly in a grouping element) but can't judge alt text or reading order either.

## Known limitations

- **Specialist notation:** tested on 19 real chemistry, medical and engineering documents
  ([report](test-reports/2026-10-11-notation-real-documents.md)). `AI_NOTATION_MAX` (100 regions by default) takes the
  first regions in page order, so a very math-heavy document keeps the layout rules' readings on later pages unless it's
  raised, as the [long-document settings](../operations/limits.md) do. Formulas get MathML since #403.
- **PDF/UA-2** is the verdict only for PDF 2.0 files, which keep PDF 2.0 and are labelled PDF/UA-2 (#409); for
  every other file it is checked for information only, as PDF 1.x files are written as PDF/UA-1. veraPDF has no
  standalone WCAG profile; WCAG and Section 508 conformance rest on PDF/UA and the AI-written alt text, tables and
  reading order, and a person should still review the results.
- **Forms** are only partly handled: fields are named from their printed labels (#341), but form-specific rules
  aren't fully covered.
- **Retained tags:** when existing tags are kept, their order is changed only on slide-like (landscape) pages, where
  it's put in the page's reading order if it clearly disagrees. On portrait pages newly tagged content is placed after
  the kept elements of the same page, and a reading-order mismatch is reported, not reordered. Hidden text is
  judged by whole boxes: text under a shape drawn over it counts as hidden only when the shape's bounding box
  covers it.
- **Scanned pages:** tables aren't detected (their cells are tagged as paragraphs); pages scanned sideways aren't
  checked for figures; handwriting hasn't been tested, so treat transcriptions of it as drafts.
- **Edge cases in tagging:** text inside form XObjects drawn more than once is tagged as one unit; rotated text in
  flattened pages keeps its position but not its rotation; glyph widths are estimated when matching text to layout
  blocks.
- **Reading order by AI** is off by default: the tested local models judged correctly ordered pages wrong; the layout
  check still flags suspicious order.
- **The engine's job store** is a shared folder. Spreading the engine over several hosts needs a shared
  (ReadWriteMany) volume, or an object-storage backend behind `app/storage.py`.
- **Not built yet:** SAML sign-in (designed, on hold), bounce handling for email, a classifier for AI levels (not
  just kinds), and further fixes on top of Adobe's tags beyond alt text, notation and fonts.
  **Updated 22 October 2026:** Adobe's improved copy also gets the PDF/UA identifier, a usable title and Scope on
  header cells (#446), and is the copy people get by default (#500); Adobe's tags are otherwise left as they are.
  PDF/UA-2 as an option for PDF 1.x files is open (#445).

## Comparing with another remediation service

A fair comparison with a commercial service (for example Adobe PDF Services, which the service can also use through
its Adobe route) needs a side-by-side test, which hasn't been done yet. A proposed protocol:

1. Pick 30–50 documents that represent real volume, including ones the other service handled poorly, plus forms, scans,
   LaTeX papers and slide exports.
2. Run each through both.
3. Score both outputs the same way:

| Measure | Tool | Weight (suggested) |
|---|---|---|
| PDF/UA-1 machine checks | veraPDF (failed checks, pass or fail) | 25% |
| An independent checker | PAC (PDF Accessibility Checker) or Acrobat's accessibility checker | 15% |
| Alt text quality | Human rating 1–5 on a sample of 10 figures per document | 20% |
| Headings, lists and tables correct | Human check in a screen reader (NVDA or VoiceOver) | 20% |
| Reading order correct | Human check, as above | 10% |
| Visual fidelity | Pixel comparison with the original | 5% |
| Job failures and unusable outputs | Count | 5% |

4. Have reviewers score blind, without knowing which service produced which file.
5. Decide the bar in advance, for example a total within 80% of the other service's with no category below 60%.

**Updated 22 October 2026:** one measure of this has been taken: PAC on the quick set through both routes (see
[PAC 2026 on the regression set](#pac-2026-on-the-regression-set-30-documents)). The engine's output passed after the
fixes; the Adobe route's findings were traced to Adobe's own tagging. The human-rated measures (alt text, headings, reading order) and
blind scoring haven't been done, so the full comparison is still to do.

## Cost model

All prices are as of October 2026. The assumptions are stated so the numbers can be redone.

### AI levels in use

**Updated 22 October 2026:** on the pilot service the paid level is Claude Haiku 5.5 writing, reviewed by Gemma 4
31B (`commercial-small-vision` with `open-judge`, both through OpenRouter); the Cloud Qwen, Sonnet and Opus levels
are switched off, not deleted. It was chosen on 13 October from paid runs on the 17-page math paper with every figure
reviewed: about $0.04 for the paper, against $1.00 for Sonnet with Gemini reviewing, with the Claude models alike on
that paper. That was one document with no figures, tables or scans; a figure-heavy and a scanned document should be
run before it's settled. The figures in the tables below are from earlier runs and don't cover this pairing. For an
organization on AWS, the same levels move to Bedrock (#463, open): a Bedrock trial writer (Amazon Nova Lite) is
configured for comparison, and Haiku on Bedrock waits on an account that allows it.

### AI fees per page, ordinary documents

Measured token counts × list prices, averaged over the 13 real documents:

| Profile | Writer / reviewer | AI cost per page | Per 10,000 pages |
|---|---|---|---|
| Local (LocalAI or Ollama) | Qwen3-VL 8B / Gemma 4 26B | $0 marginal | $0 + hardware and power |
| Cloud, open models (OpenRouter) | Qwen3-VL 32B / Gemma 4 31B | about $0.00012 | about $1.20 |
| Commercial (OpenRouter) | Claude Sonnet 5.5 / Gemini 3.8 Flash | about $0.0019 | about $19 |

### Math-heavy documents

AI cost concentrates in documents with a lot of math or specialist notation: ordinary documents send nothing to the
notation reader, while a research paper may send 80 or more formulas, each read and then reviewed. Measured on a
17-page mathematics research paper, with every figure and formula reviewed
([test report](test-reports/2026-10-07-notation-ai-tiers.md)):

| AI level | Writer / reviewer | Cost for the paper | Per page | Formulas read fully accurately |
|---|---|---|---|---|
| Local | Qwen3-VL 8B / Gemma 4 26B | $0 | $0 | about a quarter (estimated from the Cloud run, same writer) |
| Cloud | Qwen3-VL 8B / Gemma 4 31B | $0.03 | about $0.002 | 26% (nearly all the rest partly right) |
| Commercial | Claude Sonnet 5.5 / Gemini 3.8 Flash | $0.96 (about $0.45 without "review every figure") | about $0.056 (about $0.026) | 68% |

More than half the commercial cost was the "review every figure" pass over 150 formulas. For a collection where, say,
10% of pages are math-heavy, the commercial level's average rises from about $0.002 to about $0.004–0.007 per page.
Routing only math-heavy documents to the commercial level, with a *Math and science* kind of document (or **Let AI
decide**), keeps the rest at the cheaper rate.

### The kind-of-document classifier

**Let AI decide** costs about $0.0001 per document with Jev (an outside service), or nothing with a local model.

### Compute

Assumptions: 1 worker on a 4 vCPU cloud instance at about $0.17 per hour; about 3.2 s per page (the dissertation's
rate, including AI waits); 2 workers per instance.

$0.17 per hour ÷ (2 × 3,600 s ÷ 3.2 s per page) ≈ **$0.00008 per page**

### All in

Open cloud models + compute + about 50% for storage, Redis and idle time ≈ **$0.0003 per page** for ordinary
documents; with the commercial models ≈ **$0.003**, and up to a few cents per page for math-heavy documents reviewed in
full.

| Annual pages (ordinary documents) | Open cloud models | Commercial models |
|---|---|---|
| 100,000 | about $30 | about $300 |
| 1,000,000 | about $300 | about $3,000 |

**Not included:** the engineering time to make it production-ready and maintain it, hardware for local models, and
staff time for the human review list (which any tool's output needs in practice). At these volumes, those, not the
per-page cost, decide the business case.

## Reports and reviews

### Test reports

| Date | Report |
|---|---|
| 2026-10-07 | [Specialist notation read by AI: Local, Cloud and Commercial on a research paper](test-reports/2026-10-07-notation-ai-tiers.md) |
| 2026-10-07 | [AI-assisted OCR correction on damaged scans](test-reports/2026-10-07-ocr-ai-correction.md) |
| 2026-10-09 | [Lighter PDF/UA checkers compared with veraPDF](test-reports/2026-10-09-lighter-validators.md) |
| 2026-10-11 | [Chemistry, medical and engineering notation on real documents (Local AI)](test-reports/2026-10-11-notation-real-documents.md) |
| 2026-10-22 | **Updated 22 October 2026:** [PAC 2026 on the regression set](test-reports/2026-10-22-pac-regression-set.md) |

The [test reports index](test-reports/README.md) has a summary of each, and how to write one.

### Reviews

Internal reviews of the tagging process and of security (12 October 2026), a feature assessment (16 October
2026), and a CISO briefing for a campus deployment (19 October 2026), are kept with the private working copy: they name weaknesses before they're fixed, so they aren't part of
the public snapshot. What they led to is in the [build log](../design/build-log.md).

**Updated 22 October 2026:** a note on what a compromised pod would get after the hardening (20 October 2026) is kept
the same way. The security reviews' open items now carry dated **Updated** notes where they've changed since.
