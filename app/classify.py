"""Which kind of document is this? Suggests one of an AI level's kinds (or General) from the first pages' text, so
the AI best suited to it can be used.

Two backends: Jev (TypeSafe's typed-decision model, through OpenRouter's System One API; external, text only) or a
model behind the AI gateway asked to pick one. Both see the same thing: the file's name and title, the first pages'
text as the layout rules read it, and what specialist notation was found there. Never the page images.
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter
from pathlib import Path

import fitz
import httpx

from app.ai.base import AIError
from app.config import get_settings
from app.pipeline import notation
from app.pipeline.analyzer import analyze_page

GENERAL = "general"
MAX_CHARS = 12_000  # about 3,000 tokens: enough to tell, cheap, and well inside Jev's 32k


_MATHS = set("∑∏∫∬∮√∛±∓≤≥≠≈≡∝∞∂∇×÷·∈∉⊂⊆∪∩∀∃⇒⇔→↦′″°ˆ")
_GREEK = re.compile("[\u0391-\u03a9\u03b1-\u03c9\u03d1\u03d5\u03f5]")
_BLANK = re.compile(r"_{5,}|\.{8,}|\[ ?\]|☐")


def _pages_to_read(total: int, pages: int) -> list[int]:
    """The first pages (titles, forms, syllabi say what they are there) and pages spread through the rest (a
    thesis's equations start after its front matter)."""
    first = list(range(min(total, max(1, pages - 1) if total > pages else pages)))
    rest = [i for i in range(len(first), total)]
    spread = max(0, pages - len(first)) + (2 if total > 20 else 0)
    picks = [rest[int(len(rest) * (k + 1) / (spread + 1))] for k in range(spread)] if rest and spread else []
    return sorted(set(first + picks))


def sample(path: Path | str, pages: int = 3, filename: str = "") -> dict:
    """The text the classifier sees, and hints about it (specialist notation, math symbols, form fields). No AI."""
    doc = fitz.open(str(path))
    try:
        found: Counter = Counter()
        hints: Counter = Counter()
        parts: list[str] = []
        read = _pages_to_read(len(doc), pages)
        per_page = MAX_CHARS // max(1, len(read))
        for i in read:
            pa, _ = analyze_page(doc, i)
            hints["tables"] += len(pa.tables)
            hints["figures"] += len(pa.images)
            hints["form fields"] += sum(1 for _ in doc[i].widgets())
            page_text: list[str] = []
            for b in pa.blocks:
                if b.role == "Artifact":
                    continue
                if need := notation.need(b):
                    found[need.kind] += 1
                hints["math symbols"] += sum(1 for c in b.text if c in _MATHS)
                hints["Greek letters"] += len(_GREEK.findall(b.text))
                hints["lines with super- or subscripts"] += int(b.has_scripts)
                hints["blanks to fill in"] += len(_BLANK.findall(b.text))
                page_text.append((b.actual_text or b.text).strip())
            parts.append(f"[page {i + 1}]\n" + "\n".join(page_text)[:per_page])
        title = (doc.metadata or {}).get("title") or ""
        return {"pages": len(doc), "pages_read": [i + 1 for i in read], "filename": filename,
                "title": title.strip()[:300], "text": "\n".join(parts)[:MAX_CHARS], "notation": dict(found),
                "hints": {k: v for k, v in hints.items() if v}}
    finally:
        doc.close()


def _options(kinds: list[dict]) -> dict[str, str]:
    """General first: Jev leans towards the first option, and General is the safe answer when unsure."""
    out = {GENERAL: "General: anything that isn't clearly one of the other kinds"}
    for k in kinds:
        if k["key"] != GENERAL:
            out[k["key"]] = f'{k["label"]}: {k.get("description") or k["label"]}'
    return out


def _state(s: dict) -> dict:
    return {"file name": s["filename"], "title": s["title"], "pages": s["pages"],
            "specialist notation found": s["notation"] or "none", "counts on the pages read": s["hints"] or "none",
            "text of pages " + ", ".join(map(str, s["pages_read"])): s["text"]}


INSTRUCTIONS = ("Which kind of document is this? The answer picks the AI best suited to making it accessible "
                "(describing its figures, reading its tables and notation).")


async def by_jev(s: dict, kinds: list[dict], model: str | None = None) -> dict:
    cfg = get_settings()
    if cfg.ai_gateway_only:
        # Through the gateway's pass-through route, which adds the OpenRouter key (config/litellm.yaml; #361)
        url = re.sub(r"/v1/?$", "", cfg.gateway_base_url.rstrip("/")) + "/systemone"
        key = cfg.gateway_api_key
    elif not cfg.openrouter_api_key:
        raise AIError("OPENROUTER_API_KEY is not configured")
    else:
        url, key = cfg.openrouter_base_url.rstrip("/") + "/systemone", cfg.openrouter_api_key
    body = {"model": model or cfg.classifier_jev_model, "state": _state(s),
            "questions": {"kind": {"type": "choice", "instructions": INSTRUCTIONS, "criteria": _options(kinds)}}}
    try:
        async with httpx.AsyncClient(timeout=cfg.ai_timeout_s) as client:
            r = await client.post(url, json=body, headers={"Authorization": f"Bearer {key}",
                                                           "X-Title": "PDF Accessibility Remediation"})
    except httpx.TransportError as exc:
        raise AIError(f"cannot reach the classifier: {exc}") from exc
    if r.status_code >= 400:
        raise AIError(f"the classifier answered HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    a = (data.get("answers") or {}).get("kind") or {}
    usage = data.get("usage") or {}
    return {"kind": a.get("choice"), "confidence": a.get("confidence"), "probabilities": a.get("probabilities"),
            "model": data.get("model") or body["model"], "input_tokens": usage.get("input_tokens"),
            "cost_usd": usage.get("cost")}


GATEWAY_PROMPT = """Decide which kind of document this is. The answer picks the AI best suited to making it accessible
(describing its figures, reading its tables and notation).

The kinds, as key: description:
{options}

The document:
{state}

Answer with the key of one kind, and how sure you are from 0 to 1. When it isn't clearly one of the other kinds,
answer "general".
JSON schema: {{"kind": str, "confidence": float}}"""


async def by_gateway(s: dict, kinds: list[dict], model: str) -> dict:
    from app.ai.gateway import GatewayProvider
    options = _options(kinds)
    prompt = GATEWAY_PROMPT.format(options="\n".join(f"- {k}: {v}" for k, v in options.items()),
                                   state=json.dumps(_state(s), ensure_ascii=False, indent=1))
    async with GatewayProvider(model) as ai:
        ai.tags = {"purpose": "classify"}
        data = await ai.chat_json(prompt)
        tokens, cost = ai.usage.prompt_tokens, ai.usage.cost_usd
    kind = str(data.get("kind") or "").strip().lower()
    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence"))))
    except (TypeError, ValueError):
        confidence = None
    return {"kind": kind, "confidence": confidence, "probabilities": None, "model": model,
            "input_tokens": tokens, "cost_usd": cost or None}


async def classify(path: Path | str, kinds: list[dict], backend: str, model: str | None = None,
                   pages: int = 3, filename: str = "") -> dict:
    """{"kind", "confidence", "probabilities", "backend", "model", "notation", "seconds", ...}. "kind" is one of the
    keys offered (or "general"), or None when there's no text to go on or the answer wasn't one of them."""
    started = time.monotonic()
    from fastapi.concurrency import run_in_threadpool
    s = await run_in_threadpool(sample, path, pages, filename)
    out = {"backend": backend, "notation": s["notation"], "hints": s["hints"], "pages_read": s["pages_read"],
           "text_chars": len(s["text"])}
    if len(s["text"].strip()) - 10 * len(s["pages_read"]) < 40:
        return {**out, "kind": None, "confidence": None, "reason": "no_text",
                "seconds": round(time.monotonic() - started, 2)}
    if backend == "jev":
        res = await by_jev(s, kinds, model)
    elif backend == "gateway":
        if not model:
            raise AIError("no model given for the gateway classifier")
        res = await by_gateway(s, kinds, model)
    else:
        raise AIError(f"unknown classifier '{backend}'")
    offered = set(_options(kinds))
    if res["kind"] not in offered:
        res = {**res, "kind": None, "reason": "not_offered"}
    return {**out, **res, "seconds": round(time.monotonic() - started, 2)}
