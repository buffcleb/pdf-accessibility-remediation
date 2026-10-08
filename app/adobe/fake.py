"""A stand-in for Adobe PDF Services Autotag, for tests and local development without an Adobe account.

It speaks the same REST protocol as Adobe (token, assets, pre-signed upload/download, autotag,
status, delete) and "autotags" with this service's own tagger, replacing any existing tags as Adobe
does. It can also misbehave on request, to exercise the client's error handling:

  FAKE_ADOBE_THROTTLE_EVERY=n   every n-th API request answers 429 "too many requests" (Retry-After: 1)
  FAKE_ADOBE_QUOTA=true         every API request answers 429001 (out of transactions)
  FAKE_ADOBE_POLLS=n            status says "in progress" n times before "done" (default 1)
  FAKE_ADOBE_FAIL=CODE          every autotag fails with CODE (e.g. INTERNAL_SERVER_ERROR)

Run it: python -m app.adobe.fake --port 8090, then point the engine at it with
ADOBE_BASE_URL=http://localhost:8090 and any ADOBE_CLIENT_ID / ADOBE_CLIENT_SECRET.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import threading
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, Form, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse


@dataclass
class FakeBehavior:
    throttle_every: int = 0
    quota: bool = False
    polls: int = 1
    fail: str = ""

    @classmethod
    def from_env(cls) -> "FakeBehavior":
        return cls(throttle_every=int(os.environ.get("FAKE_ADOBE_THROTTLE_EVERY", "0") or 0),
                   quota=os.environ.get("FAKE_ADOBE_QUOTA", "").lower() in ("1", "true", "yes"),
                   polls=int(os.environ.get("FAKE_ADOBE_POLLS", "1") or 1),
                   fail=os.environ.get("FAKE_ADOBE_FAIL", ""))


@dataclass
class FakeState:
    root: Path
    tokens: set[str] = field(default_factory=set)
    assets: dict[str, Path] = field(default_factory=dict)
    jobs: dict[str, dict] = field(default_factory=dict)
    api_requests: int = 0
    calls: dict[str, int] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


def _xlsx(path: Path, rows: list[tuple[str, str]]) -> None:
    """A minimal one-sheet workbook, like Adobe's accessibility report."""
    def cell(v: str) -> str:
        v = v.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return f'<c t="inlineStr"><is><t>{v}</t></is></c>'

    sheet = "".join(f"<row>{cell(a)}{cell(b)}</row>" for a, b in rows)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                   '</Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                   '</Relationships>')
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                   '<sheets><sheet name="Accessibility Report" sheetId="1" r:id="r1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="r1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                   '</Relationships>')
        z.writestr("xl/worksheets/sheet1.xml",
                   '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   f"<sheetData>{sheet}</sheetData></worksheet>")


def fake_autotag(src: Path, dst: Path) -> dict:
    """Tag like Adobe would: throw away existing tags, tag from the layout. Returns figures to report."""
    import pikepdf

    from app.adobe.chunks import _strip_tags
    from app.pipeline.orchestrator import prepare_analysis
    from app.tagging.tagger import tag_document

    untagged = dst.with_suffix(".untagged.pdf")
    with pikepdf.open(src) as pdf:
        _strip_tags(pdf)
        pdf.save(untagged)
    analysis = prepare_analysis(untagged)
    # Adobe doesn't write alt text: figures come back flagged for a person to describe.
    for p in analysis.pages:
        for img in p.images:
            img.alt = None
    res = tag_document(untagged, dst, analysis, title="", language=analysis.language or "en-US")
    untagged.unlink(missing_ok=True)
    return {"figures": res.figures_with_alt + res.figures_without_alt, "pages": analysis.page_count}


def create_app(root: Path | None = None, behavior: FakeBehavior | None = None) -> FastAPI:
    state = FakeState(Path(root or tempfile.mkdtemp(prefix="fake_adobe_")))
    state.root.mkdir(parents=True, exist_ok=True)
    how = behavior or FakeBehavior.from_env()
    app = FastAPI(title="Fake Adobe PDF Services")
    app.state.fake = state
    app.state.behavior = how

    def count(name: str) -> JSONResponse | None:
        with state.lock:
            state.api_requests += 1
            state.calls[name] = state.calls.get(name, 0) + 1
            n = state.api_requests
        if how.quota:
            return JSONResponse({"error": {"code": "429001", "message": "Service usage limit reached"}}, 429)
        if how.throttle_every and n % how.throttle_every == 0:
            return JSONResponse({"error": {"code": "429050", "message": "Too many requests"}}, 429,
                                headers={"Retry-After": "1"})
        return None

    def authorize(authorization: str | None, api_key: str | None) -> None:
        token = (authorization or "").removeprefix("Bearer ").strip()
        if token not in state.tokens or not api_key:
            raise HTTPException(401, detail={"error": {"code": "UNAUTHORIZED", "message": "invalid token"}})

    @app.post("/token")
    async def token(client_id: str = Form(...), client_secret: str = Form(...)):
        if (r := count("token")) is not None:
            return r
        if not client_id or not client_secret:
            return JSONResponse({"error": "invalid_client"}, 401)
        t = uuid.uuid4().hex
        state.tokens.add(t)
        return {"access_token": t, "token_type": "bearer", "expires_in": 86399}

    @app.post("/assets")
    async def new_asset(request: Request, authorization: str | None = Header(None),
                        x_api_key: str | None = Header(None)):
        if (r := count("assets")) is not None:
            return r
        authorize(authorization, x_api_key)
        asset = f"urn:aaid:AS:UE1:{uuid.uuid4()}"
        state.assets[asset] = state.root / f"{uuid.uuid4().hex}.bin"
        return {"assetID": asset, "uploadUri": f"{str(request.base_url).rstrip('/')}/storage/{asset}"}

    @app.put("/storage/{asset}")
    async def put_blob(asset: str, request: Request):
        path = state.assets.get(asset)
        if path is None:
            raise HTTPException(404)
        path.write_bytes(await request.body())
        return Response(status_code=200)

    @app.get("/storage/{asset}")
    async def get_blob(asset: str):
        path = state.assets.get(asset)
        if path is None or not path.exists():
            raise HTTPException(404)
        return Response(path.read_bytes(), media_type="application/octet-stream")

    @app.delete("/assets/{asset}")
    async def delete_asset(asset: str, authorization: str | None = Header(None), x_api_key: str | None = Header(None)):
        if (r := count("delete")) is not None:
            return r
        authorize(authorization, x_api_key)
        path = state.assets.pop(asset, None)
        if path is not None:
            path.unlink(missing_ok=True)
            state.deleted.append(asset)
        return Response(status_code=200)

    @app.post("/operation/autotag")
    async def autotag(request: Request, authorization: str | None = Header(None),
                      x_api_key: str | None = Header(None)):
        if (r := count("autotag")) is not None:
            return r
        authorize(authorization, x_api_key)
        body = await request.json()
        src = state.assets.get(body.get("assetID", ""))
        if src is None or not src.exists():
            return JSONResponse({"error": {"code": "INVALID_ASSET", "message": "asset not found"}}, 404)
        job = uuid.uuid4().hex
        state.jobs[job] = {"status": "in progress", "polls": 0, "report": bool(body.get("generateReport"))}

        async def work() -> None:
            info = state.jobs[job]
            if how.fail:
                info.update(status="failed", error={"code": how.fail, "message": "fake failure", "status": 500})
                return
            out_asset = f"urn:aaid:AS:UE1:{uuid.uuid4()}"
            out = state.root / f"{uuid.uuid4().hex}.pdf"
            try:
                tagged = await asyncio.to_thread(fake_autotag, src, out)
            except Exception as exc:  # noqa: BLE001 - mimic Adobe's error for a PDF it can't read
                code = "PASSWORD_PROTECTED" if "password" in str(exc).lower() else "CORRUPT_DOCUMENT"
                info.update(status="failed", error={"code": code, "message": str(exc)[:200], "status": 400})
                return
            state.assets[out_asset] = out
            info["tagged-pdf"] = {"assetID": out_asset, "metadata": {"type": "application/pdf",
                                                                     "size": out.stat().st_size}}
            if info["report"]:
                rep_asset = f"urn:aaid:AS:UE1:{uuid.uuid4()}"
                rep = state.root / f"{uuid.uuid4().hex}.xlsx"
                _xlsx(rep, [("Pages", str(tagged["pages"])), ("Figures needing alternate text", str(tagged["figures"])),
                            ("Generated by", "fake Adobe (local testing)")])
                state.assets[rep_asset] = rep
                info["report"] = {"assetID": rep_asset, "metadata": {"type": "application/vnd.openxmlformats-"
                                                                             "officedocument.spreadsheetml.sheet",
                                                                     "size": rep.stat().st_size}}
            else:
                info.pop("report", None)
            info["ready"] = True

        info = state.jobs[job]
        info["task"] = asyncio.create_task(work())
        return Response(status_code=201, headers={"location": f"{str(request.base_url).rstrip('/')}"
                                                              f"/operation/autotag/{job}/status",
                                                  "x-request-id": job})

    @app.get("/operation/autotag/{job}/status")
    async def status(job: str, request: Request, authorization: str | None = Header(None),
                     x_api_key: str | None = Header(None)):
        if (r := count("status")) is not None:
            return r
        authorize(authorization, x_api_key)
        info = state.jobs.get(job)
        if info is None:
            raise HTTPException(404)
        info["polls"] += 1
        if info["status"] == "failed":
            return {"status": "failed", "error": info["error"]}
        if info["polls"] <= how.polls or not info.get("ready"):
            if not info.get("ready"):
                await asyncio.wait({info["task"]}, timeout=0.5)
            if info["status"] == "failed":
                return {"status": "failed", "error": info["error"]}
            return {"status": "in progress"}
        base = str(request.base_url).rstrip("/")
        out = {"status": "done", "tagged-pdf": {**info["tagged-pdf"],
                                               "downloadUri": f"{base}/storage/{info['tagged-pdf']['assetID']}"}}
        if isinstance(info.get("report"), dict):
            out["report"] = {**info["report"], "downloadUri": f"{base}/storage/{info['report']['assetID']}"}
        return out

    @app.get("/health")
    async def health():
        return {"ok": True, "fake": True}

    return app


def reset(app: FastAPI) -> None:
    shutil.rmtree(app.state.fake.root, ignore_errors=True)


if __name__ == "__main__":
    import argparse

    import uvicorn

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8090)
    args = ap.parse_args()
    uvicorn.run(create_app(), host=args.host, port=args.port)
