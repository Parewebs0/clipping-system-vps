"""Worker registry endpoints.

Implements the contract expected by the Windows Worker (clipping-windows-worker):
    POST /worker/register   — WorkerRegistration payload, upsert worker
    POST /worker/heartbeat  — Heartbeat payload, update state
    GET  /worker            — list workers
    GET  /worker/{id}       — get one worker

All endpoints (except health) require Bearer token.
"""
from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.auth import require_bearer
from app.db.database import get_db
from app.schemas.worker import (
    HeartbeatIn,
    WorkerOut,
    WorkerRegistrationIn,
)
from app.services.worker_service import (
    get_worker,
    list_workers,
    register_worker,
    update_heartbeat,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/worker", tags=["workers"])


@router.post("/register", response_model=WorkerOut)
def register(
    payload: WorkerRegistrationIn,
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
) -> WorkerOut:
    worker = register_worker(db, payload)
    return WorkerOut.model_validate(worker)


@router.post("/heartbeat", response_model=WorkerOut)
def heartbeat(
    payload: HeartbeatIn,
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
) -> WorkerOut:
    worker = update_heartbeat(db, payload)
    if worker is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Worker not registered. "
                "Call POST /worker/register first."
            ),
        )
    return WorkerOut.model_validate(worker)


@router.get("", response_model=List[WorkerOut])
@router.get("/", response_model=List[WorkerOut])
def list_all_workers(
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
) -> List[WorkerOut]:
    return [WorkerOut.model_validate(w) for w in list_workers(db)]


@router.get("/campaigns/{campaign_id}/logo")
def campaign_logo(
    campaign_id: int,
    _: bool = Depends(require_bearer),
):
    """PNG logo for the worker. Bearer API_TOKEN, same as the rest of /worker (#55)."""
    from app.services.logo_store import logo_file_path

    path = logo_file_path(campaign_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Logo not found")
    return FileResponse(path, media_type="image/png", filename=f"{campaign_id}.png")


@router.get("/campaigns/{campaign_id}/fonts/{font_name}")
def campaign_font(
    campaign_id: int,
    font_name: str,
    _: bool = Depends(require_bearer),
):
    """Caption font (TTF/OTF) for the worker's libass fontsdir (MOLI-54)."""
    import re as _re

    from app.services.rules import branding as branding_service

    if not _re.fullmatch(r"[A-Za-z0-9_-]{1,64}(\.ttf|\.otf)", font_name):
        raise HTTPException(status_code=400, detail="bad font name")
    path = branding_service.font_file_path(campaign_id)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Font not found")
    media = "font/ttf" if path.suffix == ".ttf" else "font/otf"
    return FileResponse(path, media_type=media, filename=path.name)


@router.get("/campaigns/{campaign_id}/branding/{side}")
def campaign_branding_media(
    campaign_id: int,
    side: str,
    _: bool = Depends(require_bearer),
):
    """Intro/outro card (image or short video) for the worker (MOLI-54)."""
    from app.services.rules import branding as branding_service

    if side not in ("intro", "outro"):
        raise HTTPException(status_code=404, detail="side must be intro or outro")
    path = branding_service.media_file_path(campaign_id, side)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Branding asset not found")
    ext = path.suffix.lower()
    media = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
        ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
    }.get(ext, "application/octet-stream")
    return FileResponse(path, media_type=media, filename=path.name)


@router.get("/{worker_id}", response_model=WorkerOut)
def get_one(
    worker_id: str,
    db: Session = Depends(get_db),
    _: bool = Depends(require_bearer),
) -> WorkerOut:
    worker = get_worker(db, worker_id)
    if worker is None:
        raise HTTPException(status_code=404, detail="Worker not found")
    return WorkerOut.model_validate(worker)
