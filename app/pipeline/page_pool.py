"""A large document's per-page work on more than one CPU core (#124).

Each page's work (its layout analysis; reading its content for the checks) depends on that page alone, so the
pages can be shared out: worker k of n takes pages k, k+n, k+2n, … (interleaved, so the heavy pages of a chapter
full of figures are spread over all of them), opens the PDF itself, and hands back its pages' results; they're put
back in page order, so what comes out is what one process would have made.

The workers are plain `python -m app.pipeline.page_pool` subprocesses, not multiprocessing: a document runs inside a
Celery prefork child, which is daemonic, and a daemonic process isn't allowed children of its own. Results come back
as a pickle file in a temporary folder that only this process and its workers use.

Anything going wrong (a worker that fails, is killed for memory, or runs past the time limit) returns None, and the
caller does the work in one process as before: slower, never different.
"""
from __future__ import annotations

import importlib
import logging
import os
import pickle
import subprocess
import sys
import tempfile
import time
from pathlib import Path

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]  # the folder holding app/


def workers_for(pages: int) -> int:
    """How many processes a document of `pages` pages gets: 1 below the threshold, else the setting (not more than
    one per page)."""
    from app.config import get_settings
    s = get_settings()
    if s.page_workers <= 1 or pages < max(2, s.page_workers_min_pages):
        return 1
    return min(s.page_workers, pages)


def run(task: str, path: Path | str, pages: int, workers: int, timeout: float | None = None,
        arg: object = None) -> list | None:
    """The results of pages 0 to pages - 1 from `task` ("module:function", called as function(path, page_numbers)
    and returning {page: result}; function(path, page_numbers, arg) when there's an `arg`, which is pickled for the
    workers), in page order, worked out by `workers` processes. None if any of them didn't finish."""
    from app.config import get_settings
    timeout = timeout or get_settings().task_time_limit_s
    started = time.monotonic()
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in (str(ROOT), os.environ.get("PYTHONPATH")) if p))
    with tempfile.TemporaryDirectory(prefix="pages-") as tmp:
        given = ""
        if arg is not None:
            given = str(Path(tmp) / "arg.pickle")
            with open(given, "wb") as fh:
                pickle.dump(arg, fh, protocol=pickle.HIGHEST_PROTOCOL)
        procs = []
        for k in range(workers):
            out = Path(tmp) / f"{k}.pickle"
            cmd = [sys.executable, "-m", "app.pipeline.page_pool", task, str(path), str(pages), str(k), str(workers),
                   str(out), given]
            procs.append((subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE), out))
        results: dict[int, object] = {}
        failed = False
        try:
            for proc, out in procs:
                try:
                    _, err = proc.communicate(timeout=max(1.0, timeout - (time.monotonic() - started)))
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.communicate()
                    log.warning("page worker for %s ran past %.0f s", task, timeout)
                    failed = True
                    break
                if proc.returncode != 0 or not out.exists():
                    tail = err.decode("utf-8", "replace").strip().splitlines()[-1:] if err else []
                    log.warning("page worker for %s stopped (exit %s): %s", task, proc.returncode, " ".join(tail))
                    failed = True
                    break
                with out.open("rb") as fh:
                    results.update(pickle.load(fh))
        finally:  # one that failed, or a time limit stopping the wait: none is left running
            for proc, _ in procs:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
        if failed:
            return None
    if sorted(results) != list(range(pages)):
        log.warning("page workers for %s gave %d of %d pages", task, len(results), pages)
        return None
    log.info("%s: %d pages in %d processes, %.1f s", task, pages, workers, time.monotonic() - started)
    return [results[n] for n in range(pages)]


def _main(task: str, path: str, pages: str, k: str, n: str, out: str, given: str = "") -> None:
    module, name = task.split(":")
    fn = getattr(importlib.import_module(module), name)
    numbers = list(range(int(k), int(pages), int(n)))
    if given:
        with open(given, "rb") as fh:
            result = fn(Path(path), numbers, pickle.load(fh))
    else:
        result = fn(Path(path), numbers)
    tmp = Path(out + ".part")
    with tmp.open("wb") as fh:
        pickle.dump(result, fh, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(out)


if __name__ == "__main__":
    _main(*sys.argv[1:8])
