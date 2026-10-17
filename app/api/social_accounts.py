"""Social accounts inventory. Tokens never live here.

POST and PATCH take the write token (``require_write_bearer``). A read token
is rejected. Unknown JSON fields, including token and secret names, are 422.
"""
from __future__ import annotations

from typing import Annotated, List

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.orm import Session

from app.auth import require_bearer, require_write_bearer
from app.db.database import get_db
from app.schemas.clip import SocialAccountCreate, SocialAccountOut, SocialAccountUpdate
from app.services.publish_gate import list_social_accounts
from app.services.social_accounts import (
    SocialAccountConflict,
    SocialAccountInvalid,
    create_social_account,
    update_social_account,
)

router = APIRouter(prefix="/social_accounts", tags=["social_accounts"])

_PLATFORM = "^(youtube|instagram|tiktok|facebook)$"
_ACCOUNT_KEY = "^[a-z0-9_]{1,64}$"


@router.get("", response_model=List[SocialAccountOut])
@router.get("/", response_model=List[SocialAccountOut])
def list_all(
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
):
    return list_social_accounts(db)


@router.post("", response_model=SocialAccountOut, status_code=201)
@router.post("/", response_model=SocialAccountOut, status_code=201)
def create_one(
    payload: SocialAccountCreate,
    db: Session = Depends(get_db),
    _: bool = Depends(require_write_bearer),
):
    try:
        return create_social_account(db, payload)
    except SocialAccountConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SocialAccountInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/{platform}/{account_key}", response_model=SocialAccountOut)
def update_one(
    payload: SocialAccountUpdate,
    platform: Annotated[str, Path(pattern=_PLATFORM)],
    account_key: Annotated[str, Path(pattern=_ACCOUNT_KEY)],
    db: Session = Depends(get_db),
    _: bool = Depends(require_write_bearer),
):
    try:
        row = update_social_account(db, platform, account_key, payload)
    except SocialAccountInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="social account not found")
    return row
