"""Pluggable AI layer.

Each provider implements a single primitive, `_chat()`, that sends a prompt plus optional
PNG images and returns text plus token usage. The accessibility tasks (alt text, tables,
reading order) are written once on top of that, so swapping providers never changes them.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Callable
from typing import Any

import httpx

from app.ai import prompts
from app.config import get_settings

log = logging.getLogger(__name__)


# Characters that have no place in document text quoted into a prompt: controls, and the bidi and zero-width
# marks that can hide text from a person reading the prompt but not from the model
_NOT_DATA = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e\u2060-\u2064\ufeff]")


# A box written as [left, top, right, bottom]
_BOX = re.compile(r"\[\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*,\s*(\d+(?:\.\d+)?)\s*\]")


def as_data(text: str, limit: int) -> str:
    """Document text the way it is quoted into a prompt: cut to the limit, with control characters stripped.
    The prompt also says, before and after it, that the text is data and never instructions."""
    return _NOT_DATA.sub("", str(text or ""))[:limit]



@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0  # reported by the AI gateway per call; 0 when the provider doesn't say

    def add(self, other: "TokenUsage") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.cost_usd += other.cost_usd


@dataclass
class ChatResult:
    text: str
    usage: TokenUsage = field(default_factory=TokenUsage)


class AIError(RuntimeError):
    pass


_RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504}


def parse_json_object(text: str) -> dict:
    """Lenient JSON extraction: strips code fences and finds the outermost object."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    try:
        val = json.loads(text)
        if isinstance(val, dict):
            return val
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    if start >= 0:
        # Output cut off by the token cap (models that loop): close the open string/object.
        body = text[start:].rstrip().rstrip(",")
        for tail in ('"}', '"]}', "]}", "}"):
            try:
                val = json.loads(body + tail)
                if isinstance(val, dict):
                    val["_truncated"] = True
                    return val
            except json.JSONDecodeError:
                continue
    raise AIError(f"model did not return JSON: {text[:200]!r}")


_PLACEHOLDER_ALT = re.compile(
    r"^\s*(no (text|description|alt( text)?|content) (available|provided|found)|n/?a|none|null|image|picture|"
    r"photo|graphic|figure|alt text|description|unknown|untitled)\s*\.?\s*$", re.I)

_REPEAT = re.compile(r"(.{4,120}?)(?:[\s,;.]*\1){2,}", re.S)


# What a description is asked to stay under, and where it is cut if it doesn't
ALT_ASKED, ALT_LIMIT = 250, 400
ALT_ASKED_LONG, ALT_LIMIT_LONG = 700, 900


def collapse_repeats(text: str, limit: int = ALT_LIMIT) -> str:
    """Remove looping repetition ("Policy Title: Policy Title: ...") and cap length at a word boundary."""
    prev = None
    while prev != text:
        prev, text = text, _REPEAT.sub(r"\1", text)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return text


class AIProvider(ABC):
    name: str = "base"

    def __init__(self, model: str, table_model: str | None = None, log_path: Path | None = None):
        settings = get_settings()
        self.model = model
        self.table_model = table_model or model
        self.usage = TokenUsage()
        self.calls = 0
        self.failures = 0
        self._sem = asyncio.Semaphore(settings.ai_concurrency)
        self._timeout = settings.ai_timeout_s
        self._retries = settings.ai_max_retries
        self.max_output_tokens = settings.ai_max_output_tokens
        self._log_path = log_path
        self._client: httpx.AsyncClient | None = None
        # Attribution sent with each call where the provider supports it (the gateway): job id, tier
        self.tags: dict[str, str] = {}

    # -- lifecycle --------------------------------------------------------
    async def __aenter__(self) -> "AIProvider":
        self._client = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    # -- provider primitive -----------------------------------------------
    async def preflight(self) -> None:
        """Raise AIError unless the provider is reachable and serves the configured models.
        Providers override this; the default (mock) always passes."""
        return None

    def _check_models(self, available: list[str], where: str) -> None:
        if not available:
            return  # endpoint did not list models; the first call will tell
        names = set(available) | {a.removesuffix(":latest") for a in available}
        missing = [m for m in {self.model, self.table_model} if m and m not in names and f"{m}:latest" not in names]
        if missing:
            shown = ", ".join(sorted(available)[:12]) + (" ..." if len(available) > 12 else "")
            raise AIError(f"model(s) {', '.join(missing)} not available at {where}; available: {shown}")

    @abstractmethod
    async def _chat(self, model: str, system: str, prompt: str, images: list[bytes]) -> ChatResult: ...

    async def chat_json(self, prompt: str, images: list[bytes] | None = None, model: str | None = None,
                        salvage: Callable[[str], dict | None] | None = None) -> dict:
        """salvage: for an answer that isn't whole JSON (cut off at the output limit), what can still be used of it."""
        model = model or self.model
        images = images or []
        last_exc: Exception | None = None
        bad_output = 0
        async with self._sem:
            for attempt in range(self._retries + 1):
                started = time.monotonic()
                try:
                    res = await self._chat(model, prompts.SYSTEM, prompt, images)
                    self.calls += 1
                    self.usage.add(res.usage)
                    try:
                        data = parse_json_object(res.text)
                    except (AIError, json.JSONDecodeError):
                        data = salvage(res.text) if salvage else None
                        if data is None:
                            raise
                    self._log(model, prompt, len(images), res, None, time.monotonic() - started)
                    return data
                except httpx.HTTPStatusError as exc:
                    body = exc.response.text[:300] if exc.response is not None else ""
                    last_exc = AIError(f"HTTP {exc.response.status_code} from {self.name}: {body}")
                    self._log(model, prompt, len(images), None, str(last_exc), time.monotonic() - started)
                    deterministic = any(k in body.lower() for k in ("context size", "mtmd", "too large"))
                    if exc.response.status_code not in _RETRYABLE or deterministic:
                        break
                except (AIError, json.JSONDecodeError) as exc:
                    last_exc = exc
                    self._log(model, prompt, len(images), None, str(exc), time.monotonic() - started)
                    bad_output += 1
                    if bad_output >= 2:
                        break  # the model answered but not usefully; repeating rarely helps
                except httpx.TransportError as exc:
                    last_exc = exc
                    self._log(model, prompt, len(images), None, str(exc), time.monotonic() - started)
                if attempt < self._retries:
                    await asyncio.sleep(min(30.0, 2.0 ** attempt))
        self.failures += 1
        raise AIError(f"{self.name} call failed: {last_exc}")

    def _log(self, model, prompt, n_images, res: ChatResult | None, error, elapsed) -> None:
        if not self._log_path:
            return
        entry: dict[str, Any] = {
            "ts": time.time(), "provider": self.name, "model": model, "images": n_images,
            "elapsed_s": round(elapsed, 2), "prompt": prompt,
        }
        if res:
            entry.update(response=res.text, usage=res.usage.__dict__)
        if error:
            entry["error"] = error
        with self._log_path.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    @staticmethod
    def b64(png: bytes) -> str:
        return base64.b64encode(png).decode()

    # -- accessibility tasks ----------------------------------------------
    async def describe_image(self, png: bytes, page: int, context: str, long: bool = False) -> dict:
        """long: a picture with several parts to say (a reaction scheme's steps): it is asked for, and allowed,
        more than a photograph's few lines. Past the limit a description is cut at a word, so the model is told
        the room it has."""
        asked, limit = (ALT_ASKED_LONG, ALT_LIMIT_LONG) if long else (ALT_ASKED, ALT_LIMIT)
        data = await self.chat_json(prompts.IMAGE_ALT.format(page=page + 1, context=as_data(context, 1500), data=prompts.DATA,
                                                             chars=asked), [png])
        alt = collapse_repeats(str(data.get("alt") or "").strip(), limit)
        if _PLACEHOLDER_ALT.match(alt):
            alt = ""  # "No text available", "N/A", "image" ... are not descriptions
        decorative = bool(data.get("decorative"))
        if not decorative and not alt:
            raise AIError("model returned no alt text for a non-decorative image")
        return {
            "decorative": decorative,
            "kind": str(data.get("kind") or "other"),
            "alt": alt[:1000],
            "latex": data.get("latex") or None,
        }

    async def read_notation(self, png: bytes, page: int, extracted: str, kind_hint: str) -> dict:
        """Specialist notation the layout rules couldn't read: transcription, LaTeX and spoken form."""
        hint = {"unreadable": "math or symbol", "math": "mathematical"}.get(kind_hint, kind_hint)
        data = await self.chat_json(prompts.NOTATION.format(kind_hint=hint, page=page + 1, extracted=as_data(extracted, 600),
                                                    data=prompts.DATA),
                                    [png])
        spoken = collapse_repeats(str(data.get("spoken") or "").strip())
        # Markup that leaks into the spoken form ("b_F", "x^2", "\\alpha") isn't speech
        spoken = re.sub(r"\s*\^\s*", " to the power of ", spoken)
        spoken = re.sub(r"(?<=\w)_(?=\w)", " sub ", spoken).replace("_", " ")
        spoken = re.sub(r"\\([A-Za-z]+)", r"\1", spoken)
        spoken = re.sub(r"[{}]", "", re.sub(r"\s{2,}", " ", spoken)).strip()
        if not spoken:
            raise AIError("model returned no spoken form for the notation")
        try:
            conf = max(0.0, min(1.0, float(data.get("confidence") or 0)))
        except (TypeError, ValueError):
            conf = 0.0
        return {
            # "maths" from models (or prompts) using British English means the same as "math"
            "kind": {"maths": "math"}.get(str(data.get("kind") or kind_hint), str(data.get("kind") or kind_hint)),
            "transcription": str(data.get("transcription") or "").strip()[:1000],
            "latex": (str(data.get("latex")).strip()[:2000] or None) if data.get("latex") else None,
            "spoken": spoken[:1500],
            "confidence": conf,
        }

    async def find_formulas(self, png: bytes, page: int) -> list[tuple[float, float, float, float]]:
        """Where a scanned page's displayed formulas are: boxes as fractions of the image (left, top, right, bottom)."""
        def complete_boxes(text: str) -> dict | None:
            # A long list cut off at the output limit (or a model repeating itself): the boxes written in full
            boxes = [[float(v) for v in m] for m in _BOX.findall(text)]
            return {"formulas": boxes} if boxes else None

        data = await self.chat_json(prompts.FORMULA_REGIONS.format(page=page + 1), [png], salvage=complete_boxes)
        found = data.get("formulas")
        out = []
        for item in found[:60] if isinstance(found, list) else []:
            box = item.get("box") if isinstance(item, dict) else item
            try:
                x0, y0, x1, y1 = (max(0.0, min(1000.0, float(v))) for v in box)
            except (TypeError, ValueError):
                continue
            box = (x0 / 1000, y0 / 1000, x1 / 1000, y1 / 1000)
            if x1 - x0 >= 5 and y1 - y0 >= 3 and box not in out:  # room for something in it, and not said before
                out.append(box)
        return out

    async def correct_ocr_line(self, png: bytes, page: int, text: str, how: str) -> dict:
        """how: "correct" (fix the [[marked]] words) or "transcribe" (read the line afresh)."""
        prompt = prompts.OCR_CORRECT if how == "correct" else prompts.OCR_TRANSCRIBE
        data = await self.chat_json(prompt.format(page=page + 1, text=as_data(text, 600), data=prompts.DATA), [png])
        out = str(data.get("text") or "").strip()
        if not out:
            raise AIError("model returned no text for the line")
        try:
            conf = max(0.0, min(1.0, float(data.get("confidence") or 0)))
        except (TypeError, ValueError):
            conf = 0.0
        return {"text": out.replace("[[", "").replace("]]", "")[:1000], "confidence": conf}

    async def name_field(self, png: bytes, page: int, kind: str, context: str) -> str:
        """A form field's label, from a picture of it and the text around it (#341). Empty if unclear."""
        data = await self.chat_json(prompts.FIELD_LABEL.format(page=page + 1, kind=kind, context=as_data(context, 600),
                                                               data=prompts.DATA), [png])
        return str(data.get("label") or "").strip()[:120]

    async def analyze_table(self, png: bytes, page: int, grid: list[list[str]]) -> dict:
        grid_txt = "\n".join(" | ".join(as_data(c, 30) for c in row[:14]) for row in grid[:30])
        data = await self.chat_json(
            prompts.TABLE.format(page=page + 1, n_rows=len(grid), n_cols=len(grid[0]) if grid else 0, grid=grid_txt,
                                 data=prompts.DATA),
            [png],
            model=self.table_model,
        )
        return {
            "header_rows": max(0, int(data.get("header_rows") or 0)),
            "header_cols": max(0, int(data.get("header_cols") or 0)),
            "spans": [s for s in data.get("spans") or [] if isinstance(s, dict)],
            "summary": str(data.get("summary") or "")[:500] or None,
            "is_layout_table": bool(data.get("is_layout_table")),
        }

    async def determine_reading_order(self, png: bytes, page: int, width: float, height: float,
                                      regions: list[dict]) -> list[str]:
        # Short numeric ids keep the answer small; small models otherwise ramble through long id lists.
        lines = "\n".join(
            f'- id={n} bbox=({r["bbox"][0]:.0f},{r["bbox"][1]:.0f},{r["bbox"][2]:.0f},{r["bbox"][3]:.0f}) '
            f'text="{as_data(r["text"], 50)}"'
            for n, r in enumerate(regions, start=1)
        )
        data = await self.chat_json(
            prompts.READING_ORDER.format(page=page + 1, width=round(width), height=round(height), regions=lines,
                                         data=prompts.DATA),
            [png],
        )
        try:
            order = [int(i) for i in data.get("order") or []]
        except (TypeError, ValueError) as exc:
            raise AIError("reading order contains non-numeric ids") from exc
        if sorted(order) != list(range(1, len(regions) + 1)):
            raise AIError("reading order is not a permutation of the given ids")
        return [regions[i - 1]["id"] for i in order]

    # -- verification judges -------------------------------------------------
    async def judge_alt(self, png: bytes, kind: str, alt: str, context: str) -> dict:
        checklist = prompts.ALT_CHECKLISTS.get(kind, prompts.ALT_DEFAULT_CHECKLIST)
        data = await self.chat_json(
            prompts.ALT_JUDGE.format(kind=kind or "unknown", alt=as_data(alt, 1000), context=as_data(context, 1200),
                                     checklist=checklist, data=prompts.DATA), [png])
        verdict = str(data.get("verdict") or "").strip().lower()
        if verdict not in ("accurate", "partially_accurate", "inaccurate", "should_be_decorative"):
            raise AIError(f"judge returned an unknown verdict: {verdict!r}")
        try:
            confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
        except (TypeError, ValueError):
            confidence = 0.5
        as_list = lambda v: [collapse_repeats(str(x), 200) for x in v][:6] if isinstance(v, list) else []  # noqa: E731
        return {
            "verdict": verdict,
            "problems": as_list(data.get("problems")),
            "hallucinated": as_list(data.get("hallucinated_details")),
            "suggested_alt": collapse_repeats(str(data.get("suggested_alt") or ""), 400) or None,
            "confidence": confidence,
        }

    async def judge_order(self, png: bytes, width: float, height: float, n_boxes: int) -> dict:
        data = await self.chat_json(prompts.ORDER_JUDGE.format(width=round(width), height=round(height)), [png])
        out_of_place = []
        for x in data.get("out_of_place") or []:
            try:
                n = int(x)
            except (TypeError, ValueError):
                continue
            if 1 <= n <= n_boxes:
                out_of_place.append(n)
        try:
            confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
        except (TypeError, ValueError):
            confidence = 0.5
        return {
            "acceptable": bool(data.get("acceptable")),
            "out_of_place": sorted(set(out_of_place))[:30],
            "explanation": collapse_repeats(str(data.get("explanation") or ""), 400),
            "confidence": confidence,
        }
