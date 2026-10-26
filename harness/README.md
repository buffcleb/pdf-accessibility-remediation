# Harness

Tools for testing the engine against folders of PDFs, outside the portal: generating sample documents, running a batch
through the engine's API with checks on every result, and measuring OCR accuracy.

| Tool | What it does |
|---|---|
| [`make_samples.py`](make_samples.py) | Generates the synthetic sample PDFs (untagged, partly tagged with broken tags, scanned, two-column, tables, unembedded fonts…). The engine's tests use the same samples |
| [`run_harness.py`](run_harness.py) | Submits every PDF in a folder to the engine, waits for each, checks the results, and writes a report |
| [`run_folder.py`](run_folder.py) | Runs every PDF in a folder through the pipeline in its own process (no engine API, no queue) and says whether each passes; used by `scripts/regression.sh` |
| [`roles_dump.py`](roles_dump.py) | Dumps what the engine makes of each document in a folder (every block's role, text hash and notes, each page's reading order) and compares two dumps, or two runs' tag maps: whether a change altered anything, and where |
| [`ocr_eval.py`](ocr_eval.py) | Damages pages of a text PDF like a poor scan, OCRs them (optionally with AI correction), and measures word accuracy |

All but `run_harness.py` need the engine's Python dependencies (`pip install -r requirements.txt`);
`run_harness.py` only needs `httpx` and a running engine.

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
| `--expect-compliant` | | Fail unless each primary output passes veraPDF (PDF/UA-1, or PDF/UA-2 for a PDF 2.0 file) |
| `--markdown` | | Save each job's Markdown report and a combined `harness_report.md` |
| `--base64` | | Ask for files inline instead of as links |
| `--debug` | | Keep the engine's working files and AI logs |
| `--timeout`, `--poll` | 900, 2 | Seconds to wait per document, and between polls |
| `--out` | `harness_output` | Where results go; use a new folder for each run |

For every document it checks that the job completed (a tag integrity failure fails the job), that the reports and
metrics are complete, that the output is tagged and has no more failed checks than the original, that the visually
exact file is pixel-identical to the original, that every file downloads, that no AI calls failed, and (with
`--expect-compliant`) that the primary output passes. On the Adobe route, more failed checks and pages that render
differently are printed as notes about Adobe's output, not counted as failures. It prints a line per file and exits 0
only if every check passed. `harness_output/<run>/harness_report.md` summarizes the run and links each document's
report.

## Regression documents

Real documents that once broke something are kept in `samples/regression/`, with a `NOTES.md` saying what went wrong
and which change fixed it. There are two folders: `full/` has each document whole; `quick/` has the same set for
everyday runs, with long documents cut down to the pages that caused trouble (and links to `full/` for the short
ones). Run as a batch after changes to the engine:

```bash
python harness/run_harness.py samples/regression/quick --provider mock --expect-compliant --out harness_output/regression_quick
python harness/run_harness.py samples/regression/full --provider mock --expect-compliant --out harness_output/regression_full
```

`scripts/regression.sh` does the same without a running engine: it runs the quick set through the working tree's
code in the engine's image (`harness/run_folder.py`; the full set every fifth run, or with `--full`; `--quick` and
`--every N` change that), prints a line per document, and exits non-zero if any fails. It runs three documents at a
time, the longest first, each in a process of its own (`--jobs N` changes how many; 1 runs them one after another):
the quick set takes about 12 minutes that way, against 31 one at a time. Memory is the limit: Docker Desktop's
(Settings > Resources) has to hold them all, and a long document's pipeline uses several processes of its own. `--provider` and `--model` run
it with real AI; those runs are never made longer than asked. The set is git-ignored, so a worktree has none: set
`REGRESSION_SAMPLES` to the folder in the checkout that has it. `REGRESSION_IMAGE` names another image to run in.

A cut-down copy must show the old problem on the code from before the fix and pass now. A document whose own tags
are kept can't be cut without changing what is tested, so its quick copy covers only what doesn't depend on them.

Like everything under `samples/`, the folder is git-ignored: real documents are often confidential, so they never
go into the repository. The automated tests reproduce each problem with a small synthetic document instead.

## Did a change alter any document?

A refactor must change nothing, and a fix should change only what it means to. `roles_dump.py` says which. Dump the
set with the code before the change and again after it, then compare:

```bash
python -m harness.roles_dump dump samples/regression/full /tmp/roles-before --jobs 4
# ...make the change...
python -m harness.roles_dump dump samples/regression/full /tmp/roles-after --jobs 4
python -m harness.roles_dump diff /tmp/roles-before /tmp/roles-after
```

`diff` prints each document that changed with the blocks whose role, text, read-aloud text, notes or box changed, the
blocks added or removed, and the pages whose reading order or columns changed, and exits 1 if anything did. The dump
runs the layout analysis only (no AI, no tagging) and holds a hash of each block's text, not the text: the
regression documents are confidential. `--with-text` writes the text too, for a dump that stays on your computer;
`diff` then shows the text that changed.

For the tags themselves, keep each document's outputs from two regression runs and compare their tag maps:

```bash
REGRESSION_KEEP=/tmp/tags-before scripts/regression.sh --full
# ...make the change...
REGRESSION_KEEP=/tmp/tags-after scripts/regression.sh --full
python -m harness.roles_dump tagmaps /tmp/tags-before /tmp/tags-after
```

Each tag is matched by its place in the tree (`Document[0]/Sect[2]/P[1]`), so a tag added near the top shows every
later sibling as moved: read the first changes of a document first.

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
