"""Run every PDF in a folder through the pipeline here (no engine API, no queue), and say for each
whether the result passes PDF/UA-1 and how long it took. Exits 1 if any fails.

Used by scripts/regression.sh, which runs it in the engine's image with the working tree's code mounted, so what is
tested is the code as it stands, not whatever a running stack was built from.

    python harness/run_folder.py FOLDER [--provider mock] [--model NAME] [--keep DIR] [--jobs N] [--pdfua 1|2|both]

--jobs N runs N documents at once, each in a process of its own, the longest first; their lines print as each
finishes. Memory is the limit: each long document's pipeline uses several processes of its own.

--keep DIR keeps each document's outputs in DIR/<document>/ (tagged files, reports, tag maps), for comparing two runs'
tags with harness/roles_dump.py tagmaps.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def run_one(f: Path, provider: str, model: str | None, keep: Path | None, pdfua: str = "1") -> tuple[bool, str]:
    """Run one document through the pipeline; its result line, and whether it passed."""
    from app.pipeline.orchestrator import JobOptions, run_remediation
    from app.storage import JobStore

    root = Path(tempfile.mkdtemp(prefix="regression-"))
    jid = str(uuid.uuid4())
    store = JobStore(jid, root=root)
    store.create()
    shutil.copyfile(f, store.input_pdf)
    t = time.monotonic()
    try:
        result = asyncio.run(run_remediation(store, JobOptions(stem="doc", job_id=jid, provider=provider, model=model,
                                                               pdfua=pdfua)))
        # The verdict's own report: PDF/UA-1, or PDF/UA-2 for a PDF 2.0 file (#409)
        part = (result["reports"]["comparison"].get("after") or {}).get("pdfua_part") or 1
        xml = next(store.output_dir.glob(f"*verapdf_after_ua{part}.xml")).read_text()
        ok = re.search(r'isCompliant="(\w+)"', xml).group(1) == "true"
        cost = result["metrics"].get("ai_cost_usd") or 0
        note = "passes" if ok else f"FAILS PDF/UA-{part}"
        line = f"{f.name[:56]:56} {note:15} {time.monotonic() - t:5.0f} s" + (f"  ${cost:.4f}" if cost else "")
    except Exception as exc:  # noqa: BLE001 - one document's failure is a result, not the end of the run
        ok = False
        line = f"{f.name[:56]:56} ERROR {type(exc).__name__}: {str(exc)[:120]}"
    if keep is not None and store.output_dir.exists():
        dest = keep / f.stem
        shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(store.output_dir, dest)
    shutil.rmtree(root, ignore_errors=True)
    return ok, line


def _longest_first(files: list[Path]) -> list[Path]:
    """The documents with most pages first, so a long one doesn't start last and leave the others waiting on it."""
    import pymupdf

    def pages(f: Path) -> int:
        try:
            with pymupdf.open(f) as doc:
                return doc.page_count
        except Exception:  # noqa: BLE001 - unreadable here: the pipeline will say so
            return 0
    return sorted(files, key=lambda f: (-pages(f), -f.stat().st_size, f.name))


def _in_child(f: Path, args) -> tuple[bool, str]:
    """run_one in a process of its own (the pipeline starts processes of its own for long documents, and a finished
    document's memory goes back), its result line read from its output."""
    cmd = [sys.executable, str(Path(__file__).resolve()), str(f), "--one", "--provider", args.provider]
    if args.model:
        cmd += ["--model", args.model]
    if args.keep is not None:
        cmd += ["--keep", str(args.keep)]
    cmd += ["--pdfua", args.pdfua]
    done = subprocess.run(cmd, capture_output=True, text=True)
    lines = [ln for ln in done.stdout.splitlines() if ln.startswith(f.name[:56])]
    if lines:
        return done.returncode == 0, lines[-1]
    why = (done.stderr.strip().splitlines() or [f"exit {done.returncode}"])[-1]
    return False, f"{f.name[:56]:56} ERROR the process ended: {why[:120]}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("folder", type=Path, help="a folder of PDFs (or, with --one, a PDF)")
    ap.add_argument("--provider", default="mock")
    ap.add_argument("--model", default=None)
    ap.add_argument("--keep", type=Path, default=None, help="keep each document's outputs in this folder")
    ap.add_argument("--jobs", type=int, default=1, help="documents run at once, each in its own process")
    ap.add_argument("--pdfua", choices=("1", "2", "both"), default="1", help="the PDF/UA part delivered (#445)")
    ap.add_argument("--one", action="store_true", help=argparse.SUPPRESS)  # run the one PDF given (a --jobs child)
    args = ap.parse_args()

    if args.one:
        ok, line = run_one(args.folder, args.provider, args.model, args.keep, args.pdfua)
        print(line, flush=True)
        return 0 if ok else 1
    files = sorted(args.folder.glob("*.pdf"))
    if not files:
        print(f"no PDFs in {args.folder}", file=sys.stderr)
        return 2
    failed, started = [], time.monotonic()
    if args.jobs > 1:
        with ThreadPoolExecutor(args.jobs) as pool:
            futures = {pool.submit(_in_child, f, args): f for f in _longest_first(files)}
            for fut in as_completed(futures):
                ok, line = fut.result()
                print(line, flush=True)
                if not ok:
                    failed.append(futures[fut].name)
    else:
        for f in files:
            ok, line = run_one(f, args.provider, args.model, args.keep, args.pdfua)
            print(line, flush=True)
            if not ok:
                failed.append(f.name)
    print(f"{len(files) - len(failed)} of {len(files)} pass, in {time.monotonic() - started:.0f} s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
