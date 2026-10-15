"""Run every PDF in a folder through the pipeline in this process (no engine API, no queue), and say for each
whether the result passes PDF/UA-1 and how long it took. Exits 1 if any fails.

Used by scripts/regression.sh, which runs it in the engine's image with the working tree's code mounted, so what is
tested is the code as it stands, not whatever a running stack was built from.

    python harness/run_folder.py FOLDER [--provider mock] [--model NAME]
"""
from __future__ import annotations

import argparse
import asyncio
import re
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("folder", type=Path)
    ap.add_argument("--provider", default="mock")
    ap.add_argument("--model", default=None)
    args = ap.parse_args()

    from app.pipeline.orchestrator import JobOptions, run_remediation
    from app.storage import JobStore

    files = sorted(args.folder.glob("*.pdf"))
    if not files:
        print(f"no PDFs in {args.folder}", file=sys.stderr)
        return 2
    failed, started = [], time.monotonic()
    for f in files:
        root = Path(tempfile.mkdtemp(prefix="regression-"))
        jid = str(uuid.uuid4())
        store = JobStore(jid, root=root)
        store.create()
        shutil.copyfile(f, store.input_pdf)
        t = time.monotonic()
        try:
            result = asyncio.run(run_remediation(store, JobOptions(stem="doc", job_id=jid, provider=args.provider,
                                                                   model=args.model)))
            xml = next(store.output_dir.glob("*verapdf_after_ua1.xml")).read_text()
            ok = re.search(r'isCompliant="(\w+)"', xml).group(1) == "true"
            cost = result["metrics"].get("ai_cost_usd") or 0
            note = "passes" if ok else "FAILS PDF/UA-1"
            print(f"{f.name[:56]:56} {note:15} {time.monotonic() - t:5.0f} s" + (f"  ${cost:.4f}" if cost else ""), flush=True)
        except Exception as exc:  # noqa: BLE001 - one document's failure is a result, not the end of the run
            ok = False
            print(f"{f.name[:56]:56} ERROR {type(exc).__name__}: {str(exc)[:120]}", flush=True)
        if not ok:
            failed.append(f.name)
        shutil.rmtree(root, ignore_errors=True)
    print(f"{len(files) - len(failed)} of {len(files)} pass, in {time.monotonic() - started:.0f} s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
