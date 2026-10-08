# Test reports

Write-ups of tests worth keeping: experiments that compared options, measured quality or cost, or changed
how the service works. [Evaluation](../README.md) is the overall picture; these are the individual
runs behind it.

| Date | Report | In short |
|---|---|---|
| 2026-10-07 | [AI-assisted OCR correction on damaged scans](2026-10-07-ocr-ai-correction.md) | On a typical poor scan, the new OCR plus Local AI correction reads 7–10% more words correctly (syllabus 0.755 → 0.860); every table cell's text now lines up with the scan. Transcribing barely legible lines is where the AI can go wrong, so those are flagged. |
| 2026-10-07 | [Specialist notation read by AI: Local, Cloud and Commercial on a research paper](2026-10-07-notation-ai-tiers.md) | The AI fallback for math works; quality follows the model. Commercial read 68% of formulas fully accurately, Cloud (the same 8B model as Local) 26%, at $0.96 and $0.03 for 17 pages. Testing also found six problems, all fixed before the comparison. |

## Writing one

Name the file `YYYY-MM-DD-short-topic.md` and add a row above. Include:

- **Question:** what the test set out to answer.
- **Setup:** documents (page counts), code version (commit or PR), AI options and models, and settings that
  matter (e.g. "review every figure").
- **Method:** how results were measured, and anything done to keep the comparison fair (e.g. the same
  reviewer for every option).
- **Results:** numbers first (time, cost, pass/fail, quality), then a few concrete examples.
- **What changed because of it:** bugs found, fixes made, settings or recommendations updated.
- **Limits:** what the test can't tell you (one document, AI reviewer rather than a person, and so on).
- **Reproducing it:** steps, and where the raw results are (job ids).

Keep wording neutral: the service is used by any organization, not only universities.

Tests that spend money on paid AI models (the Cloud and Commercial options, through OpenRouter) are run only
with the service owner's go-ahead for that run.
