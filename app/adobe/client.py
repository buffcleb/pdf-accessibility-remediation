"""Adobe PDF Services REST client for Autotag.

Flow for one PDF (from Adobe's OpenAPI description):
  POST /token                       client id + secret -> access token (cached until shortly before expiry)
  POST /assets                      -> assetID and a pre-signed uploadUri; PUT the file there
  POST /operation/autotag           {assetID, shiftHeadings, generateReport} -> 201, Location: status URL
  GET  <status URL>                 "in progress" | "done" (download URIs) | "failed" (error code)
  GET  <downloadUri>                the tagged PDF (and the optional xlsx report)
  DELETE /assets/{assetID}          clean up, best effort

Every Adobe API request goes through the shared limiter. Throttling ("too many requests") and
server errors are retried with backoff; running out of transactions (429001 / 429002) is not.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.adobe.limiter import Limiter, get_limiter
from app.config import get_settings

log = logging.getLogger(__name__)

QUOTA_CODES = {"429001": "Adobe's usage limit for this account has been reached",
               "429002": "Adobe's usage limit for this API key has been reached"}
PERMANENT_FAILURES = {"CORRUPT_DOCUMENT": "Adobe couldn't read this PDF",
                      "PASSWORD_PROTECTED": "the PDF is password protected",
                      "BAD_PDF": "Adobe couldn't read this PDF"}

_tokens: dict[tuple[str, str], tuple[str, float]] = {}  # (base url, client id) -> (token, expires at)


class AdobeError(RuntimeError):
    def __init__(self, code: str, message: str, retryable: bool = False, status: int | None = None):
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.retryable, self.status = code, message, retryable, status


@dataclass
class AutotagResult:
    tagged_pdf: Path
    report: Path | None
    adobe_job: str
    seconds: float
    requests: int = 0
    retries: int = 0
    timings: dict = field(default_factory=dict)


def _error_code(resp: httpx.Response) -> tuple[str, str]:
    """(code, message) from an Adobe error body, whichever of its shapes it uses."""
    try:
        body = resp.json()
    except ValueError:
        return str(resp.status_code), resp.text[:300]
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        return str(err.get("code") or resp.status_code), str(err.get("message") or "")
    if isinstance(body, dict):
        code = body.get("error_code") or body.get("code") or resp.status_code
        return str(code), str(body.get("message") or body.get("error_description") or body.get("error") or "")
    return str(resp.status_code), resp.text[:300]


class AdobeClient:
    def __init__(self, http: httpx.AsyncClient | None = None, limiter: Limiter | None = None,
                 sleep=asyncio.sleep):
        s = get_settings()
        self.s = s
        self.base = s.adobe_base_url.rstrip("/")
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0))
        self._own_http = http is None
        self.limiter = limiter or get_limiter()
        self.sleep = sleep
        self.requests = 0
        self.retries = 0

    async def __aenter__(self) -> "AdobeClient":
        return self

    async def __aexit__(self, *exc) -> None:
        if self._own_http:
            await self.http.aclose()

    # --- plumbing -----------------------------------------------------------------------------
    async def _token(self, refresh: bool = False) -> str:
        key = (self.base, self.s.adobe_client_id)
        cached = _tokens.get(key)
        if cached and not refresh and cached[1] > time.time() + 60:
            return cached[0]
        if not self.s.adobe_client_id or not self.s.adobe_client_secret:
            raise AdobeError("not_configured", "Adobe client id and secret aren't set")
        resp = await self._send("POST", f"{self.base}/token", auth=False,
                                data={"client_id": self.s.adobe_client_id,
                                      "client_secret": self.s.adobe_client_secret})
        body = resp.json()
        _tokens[key] = (body["access_token"], time.time() + float(body.get("expires_in") or 3600))
        return body["access_token"]

    async def _send(self, method: str, url: str, auth: bool = True, **kw) -> httpx.Response:
        """One Adobe API request, through the limiter, with retries for throttling and server errors."""
        attempt, refreshed = 0, False
        while True:
            await self.limiter.request()
            headers = dict(kw.pop("headers", {}) or {})
            if auth:
                headers["Authorization"] = f"Bearer {await self._token()}"
                headers["x-api-key"] = self.s.adobe_client_id
            self.requests += 1
            try:
                resp = await self.http.request(method, url, headers=headers, **kw)
            except httpx.TransportError as exc:
                resp, transport_error = None, exc
            else:
                transport_error = None
            kw["headers"] = {k: v for k, v in headers.items() if k not in ("Authorization", "x-api-key")}
            if resp is not None and resp.status_code < 400:
                return resp
            if resp is not None and resp.status_code == 401 and auth and not refreshed:
                refreshed = True
                await self._token(refresh=True)
                continue
            if resp is not None and resp.status_code == 429:
                code, msg = _error_code(resp)
                if code in QUOTA_CODES:
                    raise AdobeError("adobe_quota", QUOTA_CODES[code], status=429)
            retryable = transport_error is not None or resp.status_code in (429, 500, 502, 503, 504)
            if not retryable or attempt >= self.s.adobe_max_retries:
                if transport_error is not None:
                    raise AdobeError("adobe_unreachable", str(transport_error), retryable=True)
                code, msg = _error_code(resp)
                raise AdobeError(code, msg or f"HTTP {resp.status_code}", retryable=retryable, status=resp.status_code)
            attempt += 1
            self.retries += 1
            wait = min(60.0, 2 ** attempt + random.random())
            if resp is not None and (ra := resp.headers.get("Retry-After")):
                try:
                    wait = max(wait, float(ra))
                except ValueError:
                    pass
                if resp.status_code == 429:
                    await self.limiter.pause(wait)  # every worker backs off, not just this one
            log.warning("Adobe %s %s: %s, retry %d in %.0fs", method, url.split("?")[0],
                        transport_error or f"HTTP {resp.status_code}", attempt, wait)
            await self.sleep(wait)

    async def _plain(self, method: str, url: str, **kw) -> httpx.Response:
        """A request to a pre-signed storage URL (upload/download): no Adobe credentials, retried on errors."""
        for attempt in range(self.s.adobe_max_retries + 1):
            try:
                resp = await self.http.request(method, url, **kw)
                if resp.status_code < 500:
                    resp.raise_for_status()
                    return resp
            except httpx.TransportError as exc:
                if attempt >= self.s.adobe_max_retries:
                    raise AdobeError("adobe_storage_unreachable", str(exc), retryable=True) from exc
            except httpx.HTTPStatusError as exc:
                raise AdobeError("adobe_storage_error", f"HTTP {exc.response.status_code}") from exc
            self.retries += 1
            await self.sleep(min(30.0, 2 ** (attempt + 1)))
        raise AdobeError("adobe_storage_error", "storage kept failing", retryable=True)

    # --- operations ---------------------------------------------------------------------------
    async def upload(self, pdf: Path) -> str:
        resp = await self._send("POST", f"{self.base}/assets", json={"mediaType": "application/pdf"})
        body = resp.json()
        await self._plain("PUT", body["uploadUri"], content=pdf.read_bytes(),
                          headers={"Content-Type": "application/pdf"})
        return body["assetID"]

    async def start_autotag(self, asset_id: str) -> str:
        """Returns the status URL."""
        resp = await self._send("POST", f"{self.base}/operation/autotag",
                                json={"assetID": asset_id, "shiftHeadings": self.s.adobe_shift_headings,
                                      "generateReport": self.s.adobe_generate_report})
        loc = resp.headers.get("location") or resp.headers.get("Location")
        if not loc:
            raise AdobeError("adobe_protocol", "Autotag started without a status location")
        return loc if loc.startswith("http") else f"{self.base}{loc}"

    async def wait(self, status_url: str, deadline: float) -> dict:
        while True:
            resp = await self._send("GET", status_url)
            body = resp.json()
            status = str(body.get("status") or "").lower()
            if status == "done":
                return body
            if status == "failed":
                err = body.get("error") or {}
                code = str(err.get("code") or "failed")
                raise AdobeError(code, PERMANENT_FAILURES.get(code) or err.get("message") or "Autotag failed",
                                 retryable=code not in PERMANENT_FAILURES)
            if time.monotonic() > deadline:
                raise AdobeError("adobe_timeout", "Adobe didn't finish in time", retryable=True)
            await self.sleep(self.s.adobe_poll_seconds)

    async def download(self, uri: str, dst: Path) -> Path:
        resp = await self._plain("GET", uri)
        dst.write_bytes(resp.content)
        return dst

    async def delete(self, asset_id: str) -> None:
        try:
            await self._send("DELETE", f"{self.base}/assets/{asset_id}")
        except AdobeError as exc:  # Adobe removes assets after a day anyway
            log.info("couldn't delete Adobe asset %s: %s", asset_id, exc)

    async def autotag(self, pdf: Path, out_dir: Path, name: str) -> AutotagResult:
        """Tag one PDF (holding one of the shared concurrent slots) and download the results."""
        start = time.monotonic()
        req0, ret0 = self.requests, self.retries
        timings: dict[str, float] = {}
        async with self.limiter.slot():
            timings["waited_for_slot"] = round(time.monotonic() - start, 2)
            deadline = time.monotonic() + self.s.adobe_timeout_minutes * 60
            assets: list[str] = []
            try:
                t = time.monotonic()
                asset = await self.upload(pdf)
                assets.append(asset)
                timings["upload"] = round(time.monotonic() - t, 2)
                t = time.monotonic()
                status_url = await self.start_autotag(asset)
                body = await self.wait(status_url, deadline)
                timings["autotag"] = round(time.monotonic() - t, 2)
                t = time.monotonic()
                tagged = body.get("tagged-pdf") or body.get("taggedPdf") or {}
                if not tagged.get("downloadUri"):
                    raise AdobeError("adobe_protocol", "Autotag finished without a tagged PDF")
                assets += [a for a in (tagged.get("assetID"), (body.get("report") or {}).get("assetID")) if a]
                out_pdf = await self.download(tagged["downloadUri"], out_dir / f"{name}.pdf")
                report = None
                if (body.get("report") or {}).get("downloadUri"):
                    report = await self.download(body["report"]["downloadUri"], out_dir / f"{name}_report.xlsx")
                timings["download"] = round(time.monotonic() - t, 2)
            finally:
                for a in assets:
                    await self.delete(a)
        return AutotagResult(out_pdf, report, status_url.rstrip("/").rsplit("/", 2)[-2],
                             round(time.monotonic() - start, 2), self.requests - req0, self.retries - ret0, timings)
