"""Deterministic offline provider for tests and CI. Disabled unless ALLOW_MOCK_PROVIDER=true,
because its placeholder alt text must never reach a real document."""
from __future__ import annotations

import json
import re

from app.ai.base import AIProvider, ChatResult, TokenUsage


class MockProvider(AIProvider):
    name = "mock"

    def __init__(self, model: str | None = None, **kw):
        super().__init__(model or "mock-1", **kw)

    async def _chat(self, model: str, system: str, prompt: str, images: list[bytes]) -> ChatResult:
        usage = TokenUsage(len(prompt) // 4 + 85 * len(images), 20)
        if "Alt text under review" in prompt:
            alt = re.search(r'Alt text under review: """(.*?)"""', prompt, re.S)
            bad = alt and "MISMATCH" in alt.group(1)
            out = {"verdict": "inaccurate" if bad else "accurate", "problems": ["mock: alt does not match"] if bad else [],
                   "hallucinated_details": [], "suggested_alt": "Mock corrected description" if bad else "",
                   "confidence": 0.9}
        elif "numbered boxes" in prompt:
            out = {"acceptable": True, "out_of_place": [], "explanation": "mock", "confidence": 0.9}
        elif "Describe this image" in prompt:
            page = re.search(r"page (\d+)", prompt)
            out = {"decorative": False, "kind": "illustration",
                   "alt": f"Test illustration on page {page.group(1) if page else '?'}", "latex": None}
        elif "notation from page" in prompt:
            # Echo the extracted characters (with unreadable ones as "x"), so tests can steer consistency.
            m = re.search(r'read it as:\n"""(.*?)"""', prompt, re.S)
            text = (m.group(1) if m else "").replace("\ufffd", "x")
            out = {"kind": "math", "transcription": text, "latex": text, "spoken": f"AI reading of {text}",
                   "confidence": 0.9}
        elif "from a scanned page" in prompt:
            # Return the OCR text unchanged (brackets removed): tests patch this to steer corrections.
            m = re.search(r'(?:read the line as|produced):\n"""(.*?)"""', prompt, re.S)
            out = {"text": (m.group(1) if m else "").replace("[[", "").replace("]]", ""), "confidence": 0.9}
        elif "shows a table" in prompt:
            out = {"header_rows": 1, "header_cols": 0, "spans": [], "summary": "Test data table.", "is_layout_table": False}
        elif "reading order" in prompt:
            ids = re.findall(r"- id=(\d+)", prompt)
            out = {"order": [int(i) for i in ids]}
        else:
            out = {}
        return ChatResult(json.dumps(out), usage)
