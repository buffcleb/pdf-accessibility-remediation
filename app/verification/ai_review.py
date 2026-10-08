"""Layer 3: AI review of figures (alt text) and reading order, routed by content type.

Selection: everything Layer 2 flagged, plus a reproducible random sample of the rest (or all of
it with the full-review flag). Each item goes to the judge configured for its content type; the
judge should be a different model family from the generator so it does not approve its own
mistakes. AI findings are never automatic fixes, only review items.
"""
from __future__ import annotations

import asyncio
import json
import math
import logging
import random
from dataclasses import dataclass, field

import pymupdf as fitz

from app.ai.base import AIError, AIProvider
from app.ai.registry import get_provider
from app.config import get_settings
from app.pipeline.analyzer import render_png
from app.verification.model import Finding

log = logging.getLogger(__name__)


@dataclass
class FigureItem:
    element: int
    role: str
    page: int  # 0-based
    bbox: tuple
    alt: str
    context: str
    kind: str = "image"
    flagged: bool = False


@dataclass
class OrderItem:
    page: int
    boxes: list  # [(bbox, element index)] in tag order
    flagged: bool = False


@dataclass
class ReviewPlan:
    figures: list[FigureItem] = field(default_factory=list)
    pages: list[OrderItem] = field(default_factory=list)


def routes() -> dict[str, dict]:
    """Content type -> {provider, model}. 'default' applies when a type has no route."""
    s = get_settings()
    table = {"default": {"provider": s.verify_judge_provider, "model": s.verify_judge_model}}
    if s.verify_routes:
        try:
            table.update(json.loads(s.verify_routes))
        except json.JSONDecodeError:
            log.warning("VERIFY_ROUTES is not valid JSON; using the default judge only")
    return table


def select(plan: ReviewPlan, full: bool, seed: str) -> tuple[list[FigureItem], list[OrderItem], dict]:
    s = get_settings()
    rng = random.Random(seed)
    figs = [f for f in plan.figures if f.flagged]
    rest = [f for f in plan.figures if not f.flagged]
    if full:
        figs += rest
    else:
        k = math.ceil(len(rest) * s.verify_sample_rate) if rest and s.verify_sample_rate > 0 else 0
        figs += rng.sample(rest, min(len(rest), k))
    pages = [p for p in plan.pages if p.flagged or full]
    cap = s.verify_ai_max_items
    meta = {"mode": "full" if full else f"flagged + {s.verify_sample_rate:.0%} sample",
            "figures_total": len(plan.figures), "figures_flagged": sum(f.flagged for f in plan.figures),
            "figures_selected": len(figs), "pages_selected": len(pages)}
    if len(figs) + len(pages) > cap:
        meta["capped_at"] = cap
        figs = figs[:max(0, cap - len(pages))]
        pages = pages[: cap - len(figs)]
    return figs, pages, meta


async def review(pdf_path, plan: ReviewPlan, full: bool, seed: str,
                 judge_model: str | None = None, cache: dict | None = None) -> tuple[list[Finding], dict]:
    """judge_model: the job's AI tier's review model at the gateway; it replaces the default judge
    (VERIFY_ROUTES content-type routes still apply).
    cache: verdicts shared between the outputs of one job, keyed by page, position, alt text and judge, so
    the same figure in the visually exact and the modified file is judged once (the files can differ in
    glyph shapes, from substitute fonts, which doesn't change whether the alt text is right)."""
    figs, pages, meta = select(plan, full, seed)
    if not figs and not pages:
        meta["reviewed"] = 0
        return [], meta
    table = routes()
    if judge_model:
        table["default"] = {"provider": "gateway", "model": judge_model}
    providers: dict[tuple[str, str], AIProvider] = {}

    def judge_for(kind: str) -> AIProvider | None:
        route = table.get(kind) or table["default"]
        key = (route["provider"], route["model"])
        if key not in providers:
            p = get_provider(route["provider"], route["model"])
            if p is None:
                return None
            p.tags = {"job": seed, "purpose": "review"}  # spend attribution at the gateway
            p.max_output_tokens = max(p.max_output_tokens, get_settings().verify_max_output_tokens)
            providers[key] = p
        return providers[key]

    # Check every judge we are about to use before spending time rendering pages.
    needed = {f.kind for f in figs} | ({"reading_order"} if pages else set())
    for kind in needed:
        judge = judge_for(kind)
        if judge is None:
            continue
        try:
            await judge.preflight()
        except AIError as exc:
            for p in providers.values():
                await p.__aexit__(None, None, None)
            meta.update(reviewed=0, error=f"judge {judge.name}/{judge.model} unavailable: {exc}")
            return [], meta

    findings: list[Finding] = []
    counts = {"accurate": 0, "partially_accurate": 0, "inaccurate": 0, "should_be_decorative": 0,
              "order_ok": 0, "order_problem": 0, "failed": 0}
    doc = fitz.open(pdf_path)
    try:
        async def one_figure(item: FigureItem) -> None:
            judge = judge_for(item.kind)
            if judge is None:
                return
            try:
                png = render_png(doc, item.page, dpi=150, clip=item.bbox, max_px=1200)
                key = (item.page, tuple(round(v) for v in item.bbox), item.alt, item.kind, judge.model)
                if cache is not None and key in cache:
                    res = await cache[key]
                    meta["reused"] = meta.get("reused", 0) + 1
                else:
                    task = asyncio.ensure_future(judge.judge_alt(png, item.kind, item.alt, item.context))
                    if cache is not None:
                        cache[key] = task
                    res = await task
            except (AIError, ValueError, RuntimeError) as exc:
                counts["failed"] += 1
                log.info("alt review failed on page %s: %s", item.page + 1, exc)
                return
            counts[res["verdict"]] += 1
            if res["verdict"] == "accurate":
                return
            sev = "review" if res["verdict"] == "inaccurate" else "suggest"
            issue = {"inaccurate": "alt text does not match the image",
                     "partially_accurate": "alt text is incomplete or partly wrong",
                     "should_be_decorative": "image may be decorative"}[res["verdict"]]
            detail = "; ".join(res["problems"] + [f"not in image: {h}" for h in res["hallucinated"]])[:400]
            findings.append(Finding(
                layer="ai", severity=sev, issue=issue, page=item.page + 1, role=item.role, element=item.element,
                detail=f'alt: "{item.alt[:120]}"' + (f" | {detail}" if detail else ""),
                action=(f'suggested alt: "{res["suggested_alt"]}"' if res["suggested_alt"]
                        and res["verdict"] != "should_be_decorative" else "confirm, then mark as artifact"),
                confidence=round(res["confidence"], 2),
                extra={"judge": f"{judge.name}/{judge.model}", "kind": item.kind,
                       **({"suggested_alt": res["suggested_alt"]} if res["suggested_alt"]
                          and res["verdict"] != "should_be_decorative" else {})},
            ))

        async def one_page(item: OrderItem) -> None:
            judge = judge_for("reading_order")
            if judge is None:
                return
            try:
                png = _numbered_page(doc, item)
                page = doc[item.page]
                res = await judge.judge_order(png, page.rect.width, page.rect.height, len(item.boxes))
            except (AIError, ValueError, RuntimeError) as exc:
                counts["failed"] += 1
                log.info("order review failed on page %s: %s", item.page + 1, exc)
                return
            if res["acceptable"] or res["confidence"] < 0.6:
                counts["order_ok"] += 1
                return
            counts["order_problem"] += 1
            findings.append(Finding(
                layer="ai", severity="review", issue="AI judge: reading order looks wrong", page=item.page + 1,
                detail=(res["explanation"] + (f" | out of place: {res['out_of_place']}" if res["out_of_place"] else ""))[:400],
                action="reorder the tags on this page", confidence=round(res["confidence"], 2),
                extra={"judge": f"{judge.name}/{judge.model}",
                       "elements": [item.boxes[n - 1][1] for n in res["out_of_place"]]},
            ))

        await asyncio.gather(*(one_figure(f) for f in figs), *(one_page(p) for p in pages))
    finally:
        doc.close()
        for p in providers.values():
            await p.__aexit__(None, None, None)

    usage = {"prompt_tokens": sum(p.usage.prompt_tokens for p in providers.values()),
             "completion_tokens": sum(p.usage.completion_tokens for p in providers.values()),
             "cost_usd": round(sum(p.usage.cost_usd for p in providers.values()), 6)}
    if counts["failed"] and counts["failed"] == len(figs) + len(pages):
        meta["error"] = f"every AI review call failed ({counts['failed']})"
    meta.update(reviewed=len(figs) + len(pages), results=counts, ai_tokens_used=usage,
                judges=sorted({f"{p.name}/{p.model}" for p in providers.values()}))
    return findings, meta


def _numbered_page(doc: fitz.Document, item: OrderItem) -> bytes:
    """Render the page with numbered boxes in tag order (set-of-marks prompting)."""
    page = doc[item.page]
    zoom = 1.4
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    canvas = fitz.open()
    cp = canvas.new_page(width=pix.width, height=pix.height)
    cp.insert_image(cp.rect, pixmap=pix)
    for n, (bbox, _) in enumerate(item.boxes, start=1):
        r = fitz.Rect(bbox) * zoom
        cp.draw_rect(r, color=(0.85, 0.1, 0.1), width=1.5)
        # Number tag sits just outside the box's top-left corner so it never hides the content.
        w = 12 + 9 * len(str(n))
        x = max(0.0, r.x0 - w - 2) if r.x0 - w - 2 >= 0 else r.x0
        y = max(0.0, r.y0 - 2)
        tag = fitz.Rect(x, y, x + w, y + 20)
        cp.draw_rect(tag, color=(0.85, 0.1, 0.1), fill=(0.85, 0.1, 0.1))
        cp.insert_text(fitz.Point(tag.x0 + 5, tag.y1 - 5), str(n), fontsize=14, color=(1, 1, 1), fontname="hebo")
    out = cp.get_pixmap(alpha=False)
    if max(out.width, out.height) > 1600:
        scale = 1600 / max(out.width, out.height)
        out = cp.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    data = out.tobytes("png")
    canvas.close()
    return data
