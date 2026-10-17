"""Read-only storage check for worker-side cleanup.

The worker lists what it has on disk (campaign ids, asset ids, clip ids) and
asks what may go. The server never touches files and never changes state
here: it only answers from the DB.

Rules:
- asset `done`: no active job references it, and either it failed, or it is
  transcribed with >= 1 candidate and no candidate still undecided
  (pending / needs_review). Approved candidates whose render is still pending
  or running keep the asset active through the job check.
- asset `idle`: no active job references it (used only under disk pressure).
- campaign `delete`: archived and no active job for the campaign or any of
  its assets.
- clip `release`: rejected or published, and no active job references it.
  `active` clips must not be deleted even after the TTL.
Unknown ids are reported as unknown and never marked deletable.
"""
from __future__ import annotations

import uuid
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.asset import Asset, AssetStatus
from app.models.campaign import Campaign, CampaignStatus
from app.models.candidate import Candidate, CandidateStatus
from app.models.clip import Clip, ClipStatus
from app.models.job import Job, JobStatus

ACTIVE_JOB_STATUSES = (JobStatus.PENDING.value, JobStatus.ASSIGNED.value, JobStatus.PROCESSING.value)
UNDECIDED_CANDIDATE = (CandidateStatus.PENDING.value, CandidateStatus.NEEDS_REVIEW.value)
RELEASED_CLIP = (ClipStatus.REJECTED.value, ClipStatus.PUBLISHED.value)
DELETABLE_CAMPAIGN = (CampaignStatus.ARCHIVED.value,)
MAX_IDS = 5000


def _uuids(values: Iterable[Any]) -> dict[str, uuid.UUID]:
    out: dict[str, uuid.UUID] = {}
    for v in list(values or [])[:MAX_IDS]:
        try:
            out[str(v)] = uuid.UUID(str(v))
        except (TypeError, ValueError):
            continue
    return out


def _ints(values: Iterable[Any]) -> list[int]:
    out: list[int] = []
    for v in list(values or [])[:MAX_IDS]:
        try:
            out.append(int(str(v)))
        except (TypeError, ValueError):
            continue
    return out


def _active_refs(db: Session) -> tuple[set[str], set[str], set[str]]:
    """(asset_ids, campaign_ids, clip_ids) referenced by active jobs."""
    assets: set[str] = set()
    camps: set[str] = set()
    clips: set[str] = set()
    for (payload,) in db.execute(select(Job.payload).where(Job.status.in_(ACTIVE_JOB_STATUSES))):
        p = payload or {}
        if p.get("asset_id"):
            assets.add(str(p["asset_id"]))
        if p.get("campaign_id") is not None:
            camps.add(str(p["campaign_id"]))
        if p.get("clip_id"):
            clips.add(str(p["clip_id"]))
    return assets, camps, clips


def storage_check(db: Session, *, campaign_ids=(), asset_ids=(), clip_ids=()) -> dict[str, Any]:
    active_assets, active_camps, active_clips = _active_refs(db)

    assets_out: dict[str, Any] = {}
    a_ids = _uuids(asset_ids)
    if a_ids:
        rows = {str(a.id): a for a in db.query(Asset).filter(Asset.id.in_(list(a_ids.values()))).all()}
        undecided: dict[str, int] = {}
        total: dict[str, int] = {}
        for aid, status in db.query(Candidate.asset_id, Candidate.status).filter(
            Candidate.asset_id.in_(list(a_ids.values()))
        ):
            k = str(aid)
            total[k] = total.get(k, 0) + 1
            if status in UNDECIDED_CANDIDATE:
                undecided[k] = undecided.get(k, 0) + 1
        for key in a_ids:
            a = rows.get(str(a_ids[key]))
            if a is None:
                assets_out[key] = {"known": False, "done": False, "idle": False}
                continue
            idle = str(a.id) not in active_assets
            if a.status == AssetStatus.FAILED.value:
                decided = True
            else:
                k = str(a.id)
                decided = (
                    a.status == AssetStatus.TRANSCRIBED.value
                    and total.get(k, 0) > 0
                    and undecided.get(k, 0) == 0
                )
            assets_out[key] = {
                "known": True,
                "campaign_id": a.campaign_id,
                "status": a.status,
                "candidates": total.get(str(a.id), 0),
                "undecided": undecided.get(str(a.id), 0),
                "idle": idle,
                "done": idle and decided,
            }

    camps_out: dict[str, Any] = {}
    c_ids = _ints(campaign_ids)
    if c_ids:
        rows = {c.id: c for c in db.query(Campaign).filter(Campaign.id.in_(c_ids)).all()}
        camp_assets: dict[int, set[str]] = {}
        for aid, cid in db.query(Asset.id, Asset.campaign_id).filter(Asset.campaign_id.in_(c_ids)):
            camp_assets.setdefault(cid, set()).add(str(aid))
        for cid in c_ids:
            c = rows.get(cid)
            if c is None:
                camps_out[str(cid)] = {"known": False, "delete": False}
                continue
            busy = str(cid) in active_camps or bool(camp_assets.get(cid, set()) & active_assets)
            camps_out[str(cid)] = {
                "known": True,
                "status": c.status,
                "active_jobs": busy,
                "delete": c.status in DELETABLE_CAMPAIGN and not busy,
            }

    clips_out: dict[str, Any] = {}
    k_ids = _uuids(clip_ids)
    if k_ids:
        rows = {str(c.id): c for c in db.query(Clip).filter(Clip.id.in_(list(k_ids.values()))).all()}
        for key in k_ids:
            c = rows.get(str(k_ids[key]))
            if c is None:
                clips_out[key] = {"known": False, "active": False, "release": False}
                continue
            active = str(c.id) in active_clips
            clips_out[key] = {
                "known": True,
                "status": c.status,
                "active": active,
                "release": c.status in RELEASED_CLIP and not active,
            }

    return {"assets": assets_out, "campaigns": camps_out, "clips": clips_out}
