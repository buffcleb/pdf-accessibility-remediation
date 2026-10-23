"""The audit log for a SIEM that polls (#456, docs/operations/siem.md), and the export's status for Administration >
System.

    GET /api/v1/admin/audit/export?after=ID&limit=N      Authorization: Bearer <PORTAL_SIEM_PULL_TOKEN>

Events after that id (0 for the first), oldest first, as JSON lines (the objects portal/siem.py sends), once they're
two minutes old. X-Next-After is the id to ask after next time; the same one back means nothing new. The token is
for this alone: it can't sign in or call anything else. Without PORTAL_SIEM_PULL_TOKEN the address doesn't exist.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from portal import siem
from portal.config import get_portal_settings
from portal.db import get_db, system_session
from portal.deps import Principal, require_super_admin
from portal.errors import ApiError
from portal.security.tokens import constant_time_equal

router = APIRouter(tags=["audit"])


@router.get("/admin/audit/export", include_in_schema=False)
def export(request: Request, after: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=5000)):
    token = get_portal_settings().siem_pull_token
    if not token:
        raise ApiError(404, "not_found", "Not found.")
    sent = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
    if not sent or not constant_time_equal(sent, token):
        raise ApiError(401, "unauthorized", "A valid export token is needed.")
    # Every group's events: outside any person's row-level security, as the worker's export is
    with system_session("audit export") as db:
        rows = siem.events(db, after, limit)
    body = "".join(json.dumps(e, separators=(",", ":")) + "\n" for e in rows)
    return Response(body, media_type="application/x-ndjson",
                    headers={"X-Next-After": str(rows[-1]["id"] if rows else after), "Cache-Control": "no-store"})


@router.get("/admin/audit/siem")
def siem_status(p: Principal = Depends(require_super_admin), db: Session = Depends(get_db)):
    """Where the audit log goes, how far it's got, and the last error (a super admin sees every group's events)."""
    return siem.status(db)
