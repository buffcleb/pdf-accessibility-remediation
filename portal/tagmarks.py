"""Which of a version's tags were already in the document as it was uploaded.

When a document arrives tagged and its tags are kept, the tag manager shows which tags are the producer's and which
the service added: "did we do that, or did they?" A tag counts as original when it holds content the original's
tags already held: the same marked piece of the page (same number, same place, same words), or the same form field
or link. A group counts as original when anything in it does. Tags the service repaired (a role mapped, alt text
added) are still the original's.

Worked out from the original version's tag map, so it holds for every version (including ones people have edited).
The marks are written into a version's tag map when it is stored (#580), and the stored file is served as it is;
tag maps stored before that are marked when read.
"""
from __future__ import annotations

import json

# A tag map stored with its marks starts with this (dump_marked), so the viewer's request can tell without reading it
MARKED = b'{"orig_marked":true,'


def _same_place(a: list, b: list, tol: float = 2.0) -> bool:
    return len(a) == 4 and len(b) == 4 and all(abs(x - y) <= tol for x, y in zip(a, b))


def mark_original(tagmap: dict, original: dict | None) -> dict:
    """Set "orig": true on the elements of `tagmap` that were in `original` (the uploaded file's tag map). Returns
    tagmap, changed in place. Nothing is marked when the original has no tags, or was mapped before tag maps listed
    pieces of content."""
    if not original or not original.get("elements") or not original.get("pieces"):
        return tagmap
    pieces = {(p["page"], p["mcid"]): p for p in original["pieces"]}
    fields = [(e["boxes"][0]["page"], e["boxes"][0]["bbox"]) for e in original["elements"]
              if e.get("annot") and e.get("boxes")]
    els = tagmap.get("elements") or []
    orig: set[int] = set()
    for p in tagmap.get("pieces") or []:
        was = pieces.get((p["page"], p["mcid"]))
        if was is not None and was.get("kind") == p.get("kind") and _same_place(was["bbox"], p["bbox"]) \
                and (was.get("text") or "") == (p.get("text") or ""):
            orig.add(p["el"])
    for e in els:
        if e.get("annot") and e.get("boxes"):
            b = e["boxes"][0]
            if any(pg == b["page"] and _same_place(box, b["bbox"]) for pg, box in fields):
                orig.add(e["i"])
    # A group is the original's when something in it is (children come after their parents in the list)
    for e in reversed(els):
        if e["i"] in orig and e.get("parent") is not None:
            orig.add(e["parent"])
    for e in els:
        if e["i"] in orig:
            e["orig"] = True
    return tagmap


def mark_all(tagmap: dict) -> dict:
    """The uploaded file's own tag map: every tag in it is the original's."""
    for e in tagmap.get("elements") or []:
        e["orig"] = True
    return tagmap


def dump_marked(tagmap: dict) -> bytes:
    """A marked tag map as stored: MARKED first, then the map."""
    rest = {k: v for k, v in tagmap.items() if k != "orig_marked"}
    return json.dumps({"orig_marked": True, **rest}, separators=(",", ":")).encode()


def is_marked(head: bytes) -> bool:
    """Whether a stored tag map, from its first bytes, already has its marks."""
    return head.startswith(MARKED)
