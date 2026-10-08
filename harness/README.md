# Harness

Tools for testing the engine against folders of PDFs, outside the portal: generating sample documents, running a batch
through the engine's API with checks on every result, and measuring OCR accuracy.

| Tool | What it does |
|---|---|
| [`make_samples.py`](make_samples.py) | Generates the synthetic sample PDFs (untagged, partly tagged with broken tags, scanned, two-column, tables, unembedded fonts…). The engine's tests use the same samples |
| [`run_harness.py`](run_harness.py) | Submits every PDF in a folder to the engine, waits for each, checks the results, and writes a report |
| [`ocr_eval.py`](ocr_eval.py) | Damages pages of a text PDF like a poor scan, OCRs them (optionally with AI correction), and measures word accuracy |

All three need the engine's Python dependencies (`pip install -r requirements.txt`); `run_harness.py` only needs
`httpx` and a running engine.

## Sample documents

```bash
python harness/make_samples.py samples
```

writes the synthetic set to `samples/` (git-ignored, like any real documents you add there).

## Running a batch

Start the engine (see [local Docker](../docs/deployment/local-docker.md)), then:

```bash
python harness/run_harness.py samples --provider mock --expect-compliant
```

With the AI gateway and Markdown reports:

```bash
python harness/run_harness.py path/to/folder --provider gateway --model local-vision --markdown --timeout 3600 --out harness_output/my_run
```

| Option | Default | |
|---|---|---|
| `directory` | | The folder of PDFs |
| `--api` | `http://localhost:8000` | The engine |
| `--api-key` | | `X-API-Key`, if the engine requires one |
| `--provider`, `--model` | `none` | The AI provider (`gateway`, `none`, `mock`, …) and model |
| `--route` | `standard` | `adobe` tags with Adobe Autotag (use the fake Adobe for testing) |
| `--report-only` | | Check only, change nothing |
| `--expect-compliant` | | Fail unless each primary output passes veraPDF PDF/UA-1 |
| `--markdown` | | Save each job's Markdown report and a combined `harness_report.md` |
| `--base64` | | Ask for files inline instead of as links |
| `--debug` | | Keep the engine's working files and AI logs |
| `--timeout`, `--poll` | 900, 2 | Seconds to wait per document, and between polls |
| `--out` | `harness_output` | Where results go; use a new folder for each run |

For every document it checks that the job completed (a tag integrity failure fails the job), that the reports and
metrics are complete, that the output is tagged and has no more failed checks than the original, that the visually
exact file is pixel-identical to the original, that every file downloads, that no AI calls failed, and (with
`--expect-compliant`) that the primary output passes. It prints a line per file and exits 0 only if every check
passed. `harness_output/<run>/harness_report.md` summarizes the run and links
each document's report.

## OCR accuracy

`ocr_eval.py` renders chosen pages of a **text** PDF (whose text is the ground truth), damages them (`--level clean`,
`light` or `heavy`: resolution, blur, speckles and JPEG quality), OCRs them, optionally lets the AI correct the OCR, and
reports word accuracy and text-layer alignment. Run it where the Tesseract command line is installed, such as the
engine image:

```bash
python -m harness.ocr_eval path/to/text.pdf --pages 0 --level light --model local-vision
```

`--model none` measures Tesseract alone; `--seed` changes where the speckles fall (results move by a few points).
Paid models cost money: run them only when that's been agreed. The method and results are written up in the
[OCR test report](../docs/evaluation/test-reports/2026-10-07-ocr-ai-correction.md).
