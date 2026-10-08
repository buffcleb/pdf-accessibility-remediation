"""Client for the internal remediation engine API."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import BinaryIO, Protocol

import httpx

from portal.config import get_portal_settings


class EngineError(Exception):
    """The engine refused the job (don't retry)."""


class EngineUnavailable(Exception):
    """The engine couldn't be reached (retry later)."""


@dataclass
class AIChoice:
    tier: str   # the AI tier's service key
    model: str  # its gateway model
    judge: str | None = None  # its review model at the gateway (None: the engine's default judge)
    notation: bool = True  # the AI may read specialist notation the layout rules can't
    ocr: bool = True  # the AI may correct OCR words Tesseract was unsure of


class Engine(Protocol):
    def submit(self, filename: str, fileobj: BinaryIO, report_only: bool, ai: "AIChoice | None" = None,
               full_review: bool = False, route: str = "standard", branding: dict | None = None,
               no_ai: bool = False) -> str: ...
    def submit_edit(self, filename: str, fileobj: BinaryIO, edits: list[dict], review: str = "none",
                    judge: str | None = None, reference: BinaryIO | None = None) -> str: ...
    def status(self, engine_job_id: str) -> dict: ...
    def classify(self, filename: str, fileobj: BinaryIO, kinds: list[dict], backend: str, model: str | None = None,
                 pages: int = 3) -> dict: ...
    def fetch(self, path: str, out: BinaryIO) -> int: ...


class HttpEngine:
    def __init__(self) -> None:
        s = get_portal_settings()
        headers = {"X-API-Key": s.engine_api_key} if s.engine_api_key else {}
        self.client = httpx.Client(base_url=s.engine_url.rstrip("/"), headers=headers,
                                   timeout=s.engine_timeout_seconds)
        self.provider = s.engine_ai_provider
        self.model = s.engine_ai_model

    def submit(self, filename: str, fileobj: BinaryIO, report_only: bool, ai: "AIChoice | None" = None,
               full_review: bool = False, route: str = "standard", branding: dict | None = None,
               no_ai: bool = False) -> str:
        """ai: the job's AI tier (a gateway model); without one, the configured default provider is used.
        full_review: the AI review checks every figure instead of the flagged ones plus a sample.
        route: the engine route of the job's processing service (standard | adobe).
        branding: for the PDF report (service and organization name, accent color, the AI level by name).
        no_ai: no AI at all, for an audit whose AI level is set to make audits without it."""
        provider, model = (("gateway", ai.model) if ai else ("none", None) if no_ai else (self.provider, self.model))
        data = {"ai_provider": provider,
                "flags": json.dumps({"report_only": report_only, **({"full_review": True} if full_review else {}),
                                     **({"notation_ai": False} if ai and not ai.notation else {}),
                                     **({"ocr_ai": False} if ai and not ai.ocr else {}),
                                     **({"verify_ai": False, "notation_ai": False, "ocr_ai": False}
                                        if no_ai else {}),
                                     **({"report_branding": branding} if branding else {})})}
        if model:
            data["ai_model"] = model
        if route != "standard":
            data["route"] = route
        if ai:
            data["ai_tier"] = ai.tier
            if ai.judge:
                data["judge_model"] = ai.judge
        try:
            r = self.client.post("/api/v1/remediate", data=data,
                                 files={"file": (filename, fileobj, "application/pdf")})
        except httpx.HTTPError as e:
            raise EngineUnavailable(str(e)) from e
        if r.status_code >= 500 or r.status_code == 429:
            raise EngineUnavailable(f"HTTP {r.status_code}")
        if r.status_code >= 400:
            raise EngineError(_detail(r))
        return r.json()["job_id"]

    def submit_edit(self, filename: str, fileobj: BinaryIO, edits: list[dict], review: str = "none",
                    judge: str | None = None, reference: BinaryIO | None = None) -> str:
        """Apply edits (none, for a review only) and validate; review="sampled" adds the AI tag review.
        judge: the document's AI tier's review model. reference: for a corrected file, the version it replaces,
        which the visual comparison is made against."""
        data = {"edits": json.dumps(edits), "review": review}
        if judge:
            data["judge_model"] = judge
        files = {"file": (filename, fileobj, "application/pdf")}
        if reference is not None:
            files["reference"] = ("reference.pdf", reference, "application/pdf")
        try:
            r = self.client.post("/api/v1/edit", data=data, files=files)
        except httpx.HTTPError as e:
            raise EngineUnavailable(str(e)) from e
        if r.status_code >= 500 or r.status_code == 429:
            raise EngineUnavailable(f"HTTP {r.status_code}")
        if r.status_code >= 400:
            raise EngineError(_detail(r))
        return r.json()["job_id"]

    def classify(self, filename: str, fileobj: BinaryIO, kinds: list[dict], backend: str, model: str | None = None,
                 pages: int = 3) -> dict:
        """Which of these kinds the document is, from its first pages' text: {kind, confidence, ...}. Answers at once.
        EngineUnavailable when the engine or the classifier can't be reached."""
        data = {"kinds": json.dumps(kinds), "backend": backend, "pages": str(pages)}
        if model:
            data["model"] = model
        try:
            r = self.client.post("/api/v1/classify", data=data, files={"file": (filename, fileobj, "application/pdf")})
        except httpx.HTTPError as e:
            raise EngineUnavailable(str(e)) from e
        if r.status_code >= 500 or r.status_code == 429:
            raise EngineUnavailable(_detail(r))
        if r.status_code >= 400:
            raise EngineError(_detail(r))
        return r.json()

    def ai_models(self) -> list[str]:
        """Models the engine's AI gateway serves."""
        try:
            r = self.client.get("/api/v1/ai/models")
        except httpx.HTTPError as e:
            raise EngineUnavailable(str(e)) from e
        if r.status_code >= 400:
            raise EngineUnavailable(_detail(r))
        return list(r.json().get("models") or [])

    def status(self, engine_job_id: str) -> dict:
        try:
            r = self.client.get(f"/api/v1/jobs/{engine_job_id}")
        except httpx.HTTPError as e:
            raise EngineUnavailable(str(e)) from e
        if r.status_code == 404:
            raise EngineError("the engine no longer has this job")
        if r.status_code >= 400:
            raise EngineUnavailable(f"HTTP {r.status_code}")
        return r.json()

    def fetch(self, path: str, out: BinaryIO) -> int:
        """Download an engine file (path as returned by the engine, e.g. /api/v1/jobs/x/files/y)."""
        if not path.startswith("/api/v1/jobs/"):
            raise EngineError(f"unexpected file path {path!r}")
        size = 0
        try:
            with self.client.stream("GET", path) as r:
                if r.status_code >= 400:
                    raise EngineUnavailable(f"HTTP {r.status_code} for {path}")
                for chunk in r.iter_bytes(256 * 1024):
                    out.write(chunk)
                    size += len(chunk)
        except httpx.HTTPError as e:
            raise EngineUnavailable(str(e)) from e
        return size


def _detail(r: httpx.Response) -> str:
    try:
        d = r.json().get("detail")
        return d if isinstance(d, str) else json.dumps(d)
    except Exception:
        return f"HTTP {r.status_code}"


_engine: Engine | None = None


def get_engine_client() -> Engine:
    global _engine
    if _engine is None:
        _engine = HttpEngine()
    return _engine


def set_engine_client(engine: Engine | None) -> None:
    """Tests swap in a fake engine."""
    global _engine
    _engine = engine
