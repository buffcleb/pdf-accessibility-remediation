"""Say whether a change altered what the engine makes of a set of documents: every block's role and text, and every
tag in the tagged files. For refactors, which must change nothing, and for fixes, to see everything they change.

    python -m harness.roles_dump dump FOLDER OUT [--jobs N] [--with-text]
    python -m harness.roles_dump diff BEFORE AFTER [--limit N]
    python -m harness.roles_dump tagmaps BEFORE AFTER [--limit N]

dump: runs the layout analysis (no AI, no tagging) on every PDF in FOLDER and writes OUT/<name>.json: per page, its
columns and reading order; per block, its id, role, list label, notes, artifact type, box (to a tenth of a point),
and a hash of its text and read-aloud text. Documents in the regression set are confidential, so the text itself is
written only with --with-text (for a dump that stays on this computer).

diff: compares two dumps (run dump on the code before and after a change) and prints, per document, the blocks whose
role, text or other fields changed, the blocks added or removed, and the pages whose reading order changed.

tagmaps: compares two folders of tag maps (*_tagmap_primary.json, as harness/run_folder.py --keep leaves them) by
each tag's place in the tree, role and text.

diff and tagmaps exit 1 when anything changed, 0 when nothing did.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

# The block fields compared, besides role and text
_FIELDS = ("list_label", "note_mark", "note_kind", "continues", "artifact_type", "notes", "mark_notes", "bbox")


def _hash(text: str | None) -> str | None:
    return None if text is None else hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def analyse(pdf: Path, with_text: bool = False) -> dict:
    """One document's analysis, as dump writes it."""
    from app.pipeline.orchestrator import prepare_analysis

    a = prepare_analysis(pdf)
    pages = []
    for p in a.pages:
        blocks = []
        for b in p.blocks:
            row = {"id": b.id, "role": b.role, "text": _hash(b.text), "actual_text": _hash(b.actual_text),
                   "list_label": b.list_label, "note_mark": b.note_mark, "note_kind": b.note_kind,
                   "continues": b.continues, "artifact_type": b.artifact_type, "notes": list(b.notes),
                   "mark_notes": list(b.mark_notes), "bbox": [round(v, 1) for v in b.bbox]}
            if with_text:
                row["plain"] = b.text
            blocks.append(row)
        pages.append({"number": p.number, "columns": p.columns, "reading_order": list(p.reading_order),
                      "blocks": blocks})
    return {"document": pdf.name, "pages": pages}


def _dump_one(job: tuple[str, str, bool]) -> str:
    src, out, with_text = job
    try:
        data = analyse(Path(src), with_text)
    except Exception as exc:  # noqa: BLE001 - one document's failure is recorded, the rest still run
        data = {"document": Path(src).name, "error": f"{type(exc).__name__}: {exc}"[:300]}
    Path(out).write_text(json.dumps(data, ensure_ascii=False))
    return Path(src).name


def dump(folder: Path, out: Path, jobs: int = 1, with_text: bool = False) -> int:
    files = sorted(folder.glob("*.pdf"))
    if not files:
        print(f"no PDFs in {folder}", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    work = [(str(f), str(out / (f.stem + ".json")), with_text) for f in files]
    if jobs > 1:
        with ProcessPoolExecutor(jobs) as pool:
            for name in pool.map(_dump_one, work):
                print(name, flush=True)
    else:
        for w in work:
            print(_dump_one(w), flush=True)
    print(f"{len(files)} documents written to {out}")
    return 0


def compare_documents(before: dict, after: dict) -> list[str]:
    """What changed between two dumps of one document, a line each (no text: only which block, and what of it)."""
    if "error" in before or "error" in after:
        return [] if before.get("error") == after.get("error") else \
            [f"error: {before.get('error', 'none')} -> {after.get('error', 'none')}"]
    lines = []
    old = {b["id"]: (p["number"], b) for p in before["pages"] for b in p["blocks"]}
    new = {b["id"]: (p["number"], b) for p in after["pages"] for b in p["blocks"]}
    for bid in sorted(old.keys() - new.keys(), key=_order):
        lines.append(f"p{old[bid][0]} {bid} removed ({old[bid][1]['role']})")
    for bid in sorted(new.keys() - old.keys(), key=_order):
        lines.append(f"p{new[bid][0]} {bid} added ({new[bid][1]['role']})")
    for bid in sorted(old.keys() & new.keys(), key=_order):
        (pn, a), (_, b) = old[bid], new[bid]
        what = []
        if a["role"] != b["role"]:
            what.append(f"role {a['role']} -> {b['role']}")
        if a["text"] != b["text"]:
            what.append("text")
        if a["actual_text"] != b["actual_text"]:
            what.append("read-aloud text")
        what += [k for k in _FIELDS if a.get(k) != b.get(k)]
        if what:
            shown = (f": {a['plain'][:40]!r} -> {b['plain'][:40]!r}"
                     if "plain" in a and "plain" in b and a["plain"] != b["plain"] else "")
            lines.append(f"p{pn} {bid} " + ", ".join(what) + shown)
    old_pages = {p["number"]: p for p in before["pages"]}
    for p in after["pages"]:
        q = old_pages.get(p["number"])
        if q is not None and q["reading_order"] != p["reading_order"]:
            lines.append(f"p{p['number']} reading order")
        if q is not None and q["columns"] != p["columns"]:
            lines.append(f"p{p['number']} columns {q['columns']} -> {p['columns']}")
    return lines


def _order(bid: str) -> tuple:
    """p12b3 sorts after p2b10: by page, then block."""
    page, _, block = bid[1:].partition("b")
    return (int(page) if page.isdigit() else 0, int(block) if block.isdigit() else 0, bid)


def _report(changes: dict[str, list[str]], names: list[str], limit: int) -> int:
    changed = 0
    for name in names:
        lines = changes.get(name)
        if lines is None:
            print(f"{name[:60]:61} only on one side")
            changed += 1
        elif lines:
            changed += 1
            print(f"{name[:60]:61} {len(lines)} change{'s' if len(lines) != 1 else ''}")
            for line in lines[:limit]:
                print(f"    {line}")
            if len(lines) > limit:
                print(f"    ... {len(lines) - limit} more")
        else:
            print(f"{name[:60]:61} same")
    print(f"{changed} of {len(names)} documents changed")
    return 1 if changed else 0


def diff(before: Path, after: Path, limit: int = 20) -> int:
    names = sorted({f.name for f in before.glob("*.json")} | {f.name for f in after.glob("*.json")})
    changes: dict[str, list[str]] = {}
    for name in names:
        a, b = before / name, after / name
        if a.exists() and b.exists():
            changes[name] = compare_documents(json.loads(a.read_text()), json.loads(b.read_text()))
    return _report(changes, names, limit)


def _tags(tagmap: dict) -> dict[str, tuple]:
    """Each tag by its place in the tree ("Document/Sect[2]/P[1]"): (role, page, text hash)."""
    paths: dict = {}
    seen: dict[tuple, int] = {}  # (parent, role) -> how many such tags so far
    out = {}
    for e in tagmap.get("elements") or []:
        parent, role = e.get("parent"), e.get("role")
        n = seen[(parent, role)] = seen.get((parent, role), -1) + 1
        base = paths.get(parent) if parent is not None else None
        paths[e.get("i")] = path = f"{base}/{role}[{n}]" if base else f"{role}[{n}]"
        out[path] = (role, e.get("page"), _hash(e.get("text") or ""))
    return out


def compare_tagmaps(before: dict, after: dict) -> list[str]:
    a, b = _tags(before), _tags(after)
    lines = [f"{p} removed" for p in a.keys() - b.keys()] + [f"{p} added" for p in b.keys() - a.keys()]
    for p in a.keys() & b.keys():
        if a[p] != b[p]:
            what = ["page" if a[p][1] != b[p][1] else "", "text" if a[p][2] != b[p][2] else ""]
            lines.append(f"{p} " + ", ".join(w for w in what if w))
    return sorted(lines)


def tagmaps(before: Path, after: Path, limit: int = 20) -> int:
    def found(folder: Path) -> dict[str, Path]:
        # by where each lies under the folder: run_folder.py --keep names every document's files doc_*, in its own folder
        return {str(f.relative_to(folder)): f for f in folder.rglob("*_tagmap_primary.json")}
    a, b = found(before), found(after)
    names = sorted(a.keys() | b.keys())
    changes = {n: compare_tagmaps(json.loads(a[n].read_text()), json.loads(b[n].read_text()))
               for n in names if n in a and n in b}
    return _report(changes, names, limit)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump", help="analyse every PDF in a folder and write one JSON file each")
    d.add_argument("folder", type=Path)
    d.add_argument("out", type=Path)
    d.add_argument("--jobs", type=int, default=1, help="documents analysed at once (each takes its own process)")
    d.add_argument("--with-text", action="store_true", help="write each block's text too (keep such a dump local)")
    for name, what in (("diff", "two dumps"), ("tagmaps", "two folders of tag maps")):
        c = sub.add_parser(name, help=f"compare {what}; exits 1 if anything changed")
        c.add_argument("before", type=Path)
        c.add_argument("after", type=Path)
        c.add_argument("--limit", type=int, default=20, help="changes printed per document")
    args = ap.parse_args(argv)
    if args.cmd == "dump":
        return dump(args.folder, args.out, args.jobs, args.with_text)
    if args.cmd == "diff":
        return diff(args.before, args.after, args.limit)
    return tagmaps(args.before, args.after, args.limit)


if __name__ == "__main__":
    sys.exit(main())
