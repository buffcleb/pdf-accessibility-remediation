# Evaluation

How the proof of concept was tested, what it achieved, and what it costs to run. Individual experiments are written
up in [test reports](test-reports/README.md).

## Contents

- [Test method](#test-method)
- [Results](#results)
- [What still needs a person](#what-still-needs-a-person)
- [A pass is not full accessibility](#a-pass-is-not-full-accessibility)
- [Known limitations](#known-limitations)
- [Comparing with another remediation service](#comparing-with-another-remediation-service)
- [Cost model](#cost-model)

## Test method

Each document goes through the harness (`harness/run_harness.py`). For each output file it records:

- veraPDF PDF/UA-1 failed checks, before and after;
- the tag integrity check (a failure fails the job);
- pixel identity of the visually exact file against the original, page by page;
- the "Needs human review" items;
- time, AI calls, tokens and cost.

| Set | Files | Pages | What it covers |
|---|---|---|---|
| Synthetic (`harness/make_samples.py`) | 9 | about 40 | Untagged, partly tagged with broken tags, scanned, two-column, tables, a Quartz text export, unembedded fonts |
| Real documents | 13 | 431 | arXiv and journal papers (LaTeX), a dissertation, a thesis, a college accessibility plan (original and "compliant" versions), a catalog, a brochure, a policy, a math formula sheet, sheet music, image-heavy files |
| Further real documents | 30 | – | The above plus fillable forms, scans and more formula sheets, used for the classifier comparison and regression checks |

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

## Known limitations

- **Specialist notation:** chemistry, medical and engineering notation are tested on synthetic documents only.
  `AI_NOTATION_MAX` (100 regions by default) takes the first regions in page order, so very math-heavy documents keep
  the layout rules' readings on later pages. The Adobe route doesn't read notation with AI yet. MathML isn't added yet.
- **PDF/UA-2** is validated for information only. Passing it needs PDF 2.0 output and the PDF 2.0 structure namespace,
  which aren't implemented. veraPDF has no standalone WCAG profile; WCAG and Section 508 conformance rest on PDF/UA-1
  plus the AI-written alt text, tables and reading order, and a person should still review the results.
- **Forms** are only partly handled: field tooltips are added, but form-specific rules aren't fully covered.
- **Retained tags:** when existing tags are kept, newly tagged content is placed after the kept elements of the same
  page rather than interleaved. A reading-order mismatch is reported, not reordered automatically.
- **Scanned pages:** tables aren't detected (their cells are tagged as paragraphs); pages scanned sideways aren't
  checked for figures; handwriting hasn't been tested, so treat transcriptions of it as drafts.
- **Edge cases in tagging:** text inside form XObjects drawn more than once is tagged as one unit; rotated text in
  flattened pages keeps its position but not its rotation; glyph widths are estimated when matching text to layout
  blocks.
- **Reading order by AI** is off by default: the tested local models judged correctly ordered pages wrong; the layout
  check still flags suspicious order.
- **The engine's job store** is a shared folder. Spreading the engine over several hosts needs a shared
  (ReadWriteMany) volume, or an object-storage backend behind `app/storage.py`.
- **Not built yet:** SAML sign-in (designed), bounce handling for email, a classifier for AI levels (not just kinds),
  and further fixes on top of Adobe's tags beyond alt text and fonts.

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

## Cost model

All prices are as of October 2026. The assumptions are stated so the numbers can be redone.

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
