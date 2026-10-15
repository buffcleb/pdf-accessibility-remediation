#!/usr/bin/env python3
"""Say where a folder of files repeats text from the local sample documents.

    scripts/sample_text_check.py TREE [SAMPLES]

The documents in samples/ are other people's and stay on this machine. A sentence copied from one into a test or a
document would leave with the next commit or public snapshot, so this compares every text file under TREE with
the text of every PDF under SAMPLES (default: samples/regression beside this script's repository) and lists the
places that share a run of words. It prints file names and line numbers only, never the text itself, and exits 1
if it found any.

A "run" is RUN_LENGTH words in a row of which at least REAL_WORDS are ordinary words: runs of numbers, single
letters and symbols repeat by chance (page numbers, table rows, "a b c d"), sentences don't. Text that is only a
picture in the PDF (a scan without a text layer) can't be compared, and neither can a quote shorter than a run.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

RUN_LENGTH = 7
REAL_WORDS = 4


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _real(word: str) -> bool:
    return word.isalpha() and len(word) >= 3


def runs_of(tokens: list[str]):
    for i in range(len(tokens) - RUN_LENGTH + 1):
        run = tokens[i:i + RUN_LENGTH]
        if sum(map(_real, run)) >= REAL_WORDS:
            yield i, " ".join(run)


def sample_runs(samples: Path) -> tuple[dict[str, str], list[str]]:
    """Every run in the documents, with the first document it was seen in; and the documents with no text to read."""
    import pymupdf

    seen: dict[str, str] = {}
    unread: list[str] = []
    for path in sorted(samples.rglob("*.pdf")):
        tokens: list[str] = []
        try:
            with pymupdf.open(path) as doc:
                for page in doc:
                    tokens += words(page.get_text())
        except Exception:  # a broken file is a regression document too: say so and go on
            unread.append(path.name)
            continue
        if len(tokens) < RUN_LENGTH and path.name not in unread:  # the same file may be in two sets
            unread.append(path.name)
        for _, run in runs_of(tokens):
            seen.setdefault(run, path.name)
    return seen, unread


def shared(tree: Path, seen: dict[str, str]) -> list[tuple[str, int, str]]:
    """(file, line, document) for every line of a text file under `tree` that starts or continues a shared run."""
    found = []
    for path in sorted(p for p in tree.rglob("*") if p.is_file() and ".git" not in p.relative_to(tree).parts):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue  # not text
        tokens: list[str] = []
        line_of: list[int] = []
        for number, line in enumerate(lines, 1):
            ws = words(line)
            tokens += ws
            line_of += [number] * len(ws)
        hits: dict[int, str] = {}
        for i, run in runs_of(tokens):
            if run in seen:
                hits.setdefault(line_of[i], seen[run])
        found += [(str(path.relative_to(tree)), number, name) for number, name in sorted(hits.items())]
    return found


def main(argv: list[str]) -> int:
    if not 2 <= len(argv) <= 3:
        print(__doc__.split("\n\n")[1].strip(), file=sys.stderr)
        return 2
    tree = Path(argv[1])
    samples = Path(argv[2]) if len(argv) == 3 else Path(__file__).resolve().parent.parent / "samples" / "regression"
    if not samples.is_dir():
        print(f"sample text check: no folder {samples}", file=sys.stderr)
        return 2
    seen, unread = sample_runs(samples)
    if unread:
        print(f"sample text check: no text to compare in {len(unread)} document(s): {', '.join(unread)}", file=sys.stderr)
    found = shared(tree, seen)
    for file, number, name in found:
        print(f"{file}:{number}: shares a run of {RUN_LENGTH} words with {name}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
