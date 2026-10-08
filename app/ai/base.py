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
from typing import Any

import httpx

from app.ai import prompts
from app.config import get_settings

log = logging.getLogger(__name__)


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


def collapse_repeats(text: str, limit: int = 400) -> str:
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

    async def chat_json(self, prompt: str, images: list[bytes] | None = None, model: str | None = None) -> dict:
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
                    data = parse_json_object(res.text)
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
    async def describe_image(self, png: bytes, page: int, context: str) -> dict:
        data = await self.chat_json(prompts.IMAGE_ALT.format(page=page + 1, context=context[:1500]), [png])
        alt = collapse_repeats(str(data.get("alt") or "").strip())
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
        data = await self.chat_json(prompts.NOTATION.format(kind_hint=hint, page=page + 1, extracted=extracted[:600]),
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

    async def correct_ocr_line(self, png: bytes, page: int, text: str, how: str) -> dict:
        """how: "correct" (fix the [[marked]] words) or "transcribe" (read the line afresh)."""
        prompt = prompts.OCR_CORRECT if how == "correct" else prompts.OCR_TRANSCRIBE
        data = await self.chat_json(prompt.format(page=page + 1, text=text[:600]), [png])
        out = str(data.get("text") or "").strip()
        if not out:
            raise AIError("model returned no text for the line")
        try:
            conf = max(0.0, min(1.0, float(data.get("confidence") or 0)))
        except (TypeError, ValueError):
            conf = 0.0
        return {"text": out.replace("[[", "").replace("]]", "")[:1000], "confidence": conf}

    async def analyze_table(self, png: bytes, page: int, grid: list[list[str]]) -> dict:
        grid_txt = "\n".join(" | ".join(c[:30] for c in row[:14]) for row in grid[:30])
        data = await self.chat_json(
            prompts.TABLE.format(page=page + 1, n_rows=len(grid), n_cols=len(grid[0]) if grid else 0, grid=grid_txt),
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
            f'text="{r["text"][:50]}"'
            for n, r in enumerate(regions, start=1)
        )
        data = await self.chat_json(
            prompts.READING_ORDER.format(page=page + 1, width=round(width), height=round(height), regions=lines),
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
            prompts.ALT_JUDGE.format(kind=kind or "unknown", alt=alt[:1000], context=context[:1200],
                                     checklist=checklist), [png])
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
