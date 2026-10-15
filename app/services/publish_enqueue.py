"""Create publish jobs for approve_publish clips. No LLM. MOLI-53.

Every pending ClipPublication platform is served, not only YouTube: the
human gate (publish_gate.approve_clip_publish) marks the campaign RuleSet
platforms and this enqueue queues one ``publish`` job per (clip, platform)
with per-platform copy, the healthy ``social_accounts`` of that platform in
the payload, and — when ``PUBLISH_SLOT_<PLATFORM>`` is set — a UTC
``scheduled_at`` that holds the job back (``job.available_at``; the claim
query refuses jobs scheduled in the future and the worker re-checks it).

Dry-run stays the default: without an explicit live run the job payload
carries ``dry_run: true`` and the worker only moves the clip into
``uploaded/`` and fabricates a dry-run URL. No real post without an explicit
operator-approved live run.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.candidate import Candidate
from app.models.clip import Clip
from app.models.clip_publication import ClipPublication
from app.models.job import Job
from app.models.social_account import SocialAccount
from app.logctx import format_ids
from app.services.job_service import create_job
from app.services.publish_copy import paid_promotion, platform_copy, platform_mentions
from app.services.publish_schedule import scheduled_at_for

logger = logging.getLogger(__name__)

OPEN_JOB_STATUSES = ("pending", "assigned", "processing")
# Milestone first, then the rest in a fixed readable order; unknown pending
# platforms go last, alphabetical.
PLATFORM_ORDER = tuple("youtube instagram tiktok facebook".split())
MILESTONE_PLATFORM = "youtube"
OK_LOCATIONS = ("pending_upload", "uploaded")


def _env_dry_run(cli_dry_run: bool) -> bool:
    if cli_dry_run:
        return True
    raw = (os.environ.get("PUBLISH_DRY_RUN") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _blocks_requeue(db: Session, clip_id: uuid.UUID, platform: str, *, live: bool) -> bool:
    rows = (
        db.query(Job)
        .filter(Job.job_type == "publish")
        .filter(Job.payload["clip_id"].astext == str(clip_id))
        .filter(Job.payload["platform"].astext == platform)
        .all()
    )
    for job in rows:
        if job.status in OPEN_JOB_STATUSES:
            return True
        if job.status == "failed" and not live:
            return True
        if job.status == "completed":
            res = job.result if isinstance(job.result, dict) else {}
            was_dry = bool(res.get("dry_run", True))
            if live and was_dry:
                continue
            if res.get("source_moved") or not was_dry:
                return True
    return False


def _healthy_accounts(db: Session, platform: str) -> list[SocialAccount]:
    return list(
        db.execute(
            select(SocialAccount)
            .where(SocialAccount.platform == platform)
            .where(SocialAccount.status == "healthy")
            .order_by(SocialAccount.account_key)
        ).scalars()
    )


def _pending_platforms(db: Session) -> list[str]:
    """Every platform that still has a pending publication, in PLATFORM_ORDER."""
    seen = {
        row[0]
        for row in db.execute(
            select(ClipPublication.platform)
            .where(ClipPublication.status == "pending")
            .distinct()
        ).all()
    }
    named = [p for p in PLATFORM_ORDER if p in seen]
    return named + sorted(seen - set(PLATFORM_ORDER))


def enqueue_publish_jobs(
    db: Session,
    *,
    limit: int = 1,
    platform: Optional[str] = None,
    dry_run: bool = False,
    create_jobs: bool = True,
) -> list[dict]:
    """Queue publish jobs. `platform=None` (default) serves every pending
    platform ("per campaign platform", MOLI-53); a platform name scopes the
    run (legacy `--platform youtube` behaviour). `limit` is per platform.
    `create_jobs=False` prints what a run would queue without inserting."""
    payload_dry = _env_dry_run(dry_run)
    live = not payload_dry
    if platform and isinstance(platform, str):
        platforms = [platform.strip().lower()]
    else:
        platforms = _pending_platforms(db)
    if not platforms:
        return []

    now = datetime.now(timezone.utc)
    out: list[dict] = []
    for plat in platforms:
        accounts = _healthy_accounts(db, plat)
        if not accounts:
            out.append({
                "skipped": True,
                "platform": plat,
                "reason": f"blocked_missing_account:{plat}",
            })
            continue
        scheduled = scheduled_at_for(plat, now)
        account_keys = [a.account_key for a in accounts]

        pubs = list(
            db.execute(
                select(ClipPublication)
                .where(ClipPublication.platform == plat)
                .where(ClipPublication.status == "pending")
                .order_by(ClipPublication.created_at.asc())
            ).scalars()
        )
        made = 0
        for pub in pubs:
            if made >= max(0, limit):
                break
            clip = db.get(Clip, pub.clip_id)
            if clip is None:
                continue
            if clip.publish_approved_at is None:
                continue
            if clip.location not in OK_LOCATIONS:
                continue
            if clip.qa_status != "pass" or clip.status != "approved":
                continue
            if clip.compliance_status != "pass":  # #43 post-render rules verifier
                continue
            if _blocks_requeue(db, clip.id, plat, live=live):
                continue

            campaign = db.get(Campaign, clip.campaign_id)
            cand = db.get(Candidate, clip.candidate_id) if clip.candidate_id else None
            title, description, hashtags = platform_copy(campaign, clip, cand, plat)
            file_path = clip.final_path_worker or clip.file_path
            payload = {
                "clip_id": str(clip.id),
                "campaign_id": clip.campaign_id,
                "publication_id": str(pub.id),
                "platform": plat,
                "file_path": file_path,
                "platforms": [plat],
                "caption": description,
                "hashtags": hashtags,
                "mentions": platform_mentions(campaign, plat),
                # #45: YouTube paidProductPlacementDetails.hasPaidProductPlacement
                "paid_promotion": paid_promotion(campaign),
                "title": title,
                # MOLI-53: healthy accounts for this platform; the primary posts.
                "account": accounts[0].account_key,
                "accounts": account_keys,
                "scheduled_at": scheduled.isoformat(),
                "dry_run": payload_dry,
            }
            row = {
                "clip_id": str(clip.id),
                "campaign_id": clip.campaign_id,
                "platform": plat,
                "file_path": file_path,
                "title": title,
                "account": accounts[0].account_key,
                "scheduled_at": scheduled.isoformat(),
                "dry_run": payload_dry,
            }
            if not create_jobs:
                row["job_id"] = None
                row["would_create"] = True
                out.append(row)
                made += 1
                continue

            job = create_job(
                db,
                job_type="publish",
                payload=payload,
                priority=4,
                max_attempts=3,
                available_at=scheduled if not payload_dry else None,
            )
            pub.job_id = job.id
            db.commit()
            row["job_id"] = str(job.id)
            out.append(row)
            made += 1
            logger.info(
                "enqueued publish %s platform=%s dry_run=%s scheduled_at=%s account=%s",
                format_ids(
                    job_id=job.id,
                    job_type="publish",
                    clip_id=clip.id,
                    campaign_id=clip.campaign_id,
                ),
                plat,
                payload_dry,
                scheduled.isoformat(),
                accounts[0].account_key,
            )
            if scheduled > now:
                logger.info(
                    "publish job %s held back until %s (slot %s)",
                    job.id, scheduled.isoformat(), plat,
                )
    return out
