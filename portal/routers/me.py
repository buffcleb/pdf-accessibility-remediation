from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from portal.db import get_db
from portal.deps import Principal, current_principal
from portal.schemas import MeOut, me_out

router = APIRouter(tags=["me"])


@router.get("/me", response_model=MeOut)
def me(p: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return me_out(db, p.user)
