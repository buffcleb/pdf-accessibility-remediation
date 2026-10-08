"""Super-admin routes: see the queues, pause and resume them, and retry or cancel single items."""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from portal import queues
from portal.db import get_db
from portal.deps import Principal, require_super_admin

router = APIRouter(prefix="/admin/queues", tags=["super admin"])


class PauseOut(BaseModel):
    by: str
    at: datetime
    note: str | None = None


class QueueOut(BaseModel):
    key: str
    label: str
    kind: str
    waiting: int
    active: int
    failed: int  # last 7 days
    sent: int | None = None  # email: last 7 days
    oldest_waiting: datetime | None = None
    paused: PauseOut | None = None


class ItemOut(BaseModel):
    id: str
    job_id: str | None = None
    title: str
    who: str
    pages: int | None = None
    options: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    detail: str | None = None
    error_code: str | None = None
    can_retry: bool
    can_cancel: bool
    can_process_now: bool = False


class ItemsOut(BaseModel):
    queue: str
    state: str
    items: list[ItemOut]


class PauseIn(BaseModel):
    note: str | None = Field(None, max_length=500)


@router.get("", response_model=list[QueueOut])
def list_queues(p: Principal = Depends(require_super_admin), db: Session = Depends(get_db)):
    return queues.overview(db)


@router.get("/{key}/items", response_model=ItemsOut)
def list_items(key: str, state: str = "waiting", p: Principal = Depends(require_super_admin),
               db: Session = Depends(get_db)):
    return queues.items(db, key, state)


@router.post("/{key}/pause", response_model=list[QueueOut])
def pause(key: str, request: Request, body: PauseIn | None = None, p: Principal = Depends(require_super_admin),
          db: Session = Depends(get_db)):
    """Start nothing new from this queue until it's resumed; work already started finishes."""
    queues.pause(db, request, key, p.user, body.note if body else None)
    db.commit()
    return queues.overview(db)


@router.post("/{key}/resume", response_model=list[QueueOut])
def resume(key: str, request: Request, p: Principal = Depends(require_super_admin), db: Session = Depends(get_db)):
    queues.resume(db, request, key, p.user)
    db.commit()
    return queues.overview(db)


@router.post("/{key}/items/{item_id}/retry", status_code=204)
def retry(key: str, item_id: str, request: Request, p: Principal = Depends(require_super_admin),
          db: Session = Depends(get_db)):
    queues.retry(db, request, key, item_id, p.user)
    db.commit()


@router.post("/{key}/items/{item_id}/cancel", status_code=204)
def cancel(key: str, item_id: str, request: Request, p: Principal = Depends(require_super_admin),
           db: Session = Depends(get_db)):
    queues.cancel(db, request, key, item_id, p.user)
    db.commit()


@router.post("/{key}/items/{item_id}/process-now", status_code=204)
def process_now(key: str, item_id: str, request: Request, p: Principal = Depends(require_super_admin),
                db: Session = Depends(get_db)):
    """A waiting large document goes next, ahead of the large-document rules."""
    queues.process_now(db, request, key, item_id, p.user)
    db.commit()
