"""Human gate before a clip can enter the publish enqueue tick."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.clip import Clip
from app.models.clip_publication import ClipPublication
from app.models.social_account import SOCIAL_PLATFORM_VALUES, SocialAccount

logger = logging.getLogger(__name__)

DEFAULT_PLATFORMS = ("youtube",)
OK_LOCATIONS = ("pending_upload", "uploaded")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PublishGateError(ValueError):
    """Raised when the clip is not eligible for publish approval."""


def list_social_accounts(db: Session) -> list[SocialAccount]:
    return list(db.execute(select(SocialAccount).order_by(SocialAccount.platform)).scalars())


def list_publications_for_clip(db: Session, clip_id: uuid.UUID) -> list[ClipPublication]:
    stmt = (
        select(ClipPublication)
        .where(ClipPublication.clip_id == clip_id)
        .order_by(ClipPublication.platform)
    )
    return list(db.execute(stmt).scalars())


def _review_indexes(checks: list) -> list[int]:
    return [i for i, c in enumerate(checks) if isinstance(c, dict) and c.get("status") == "review"]


def require_review_confirmations(report: dict | None, confirmed: Optional[list] = None) -> list[int]:
    """Every review row must be confirmed by index, or by a rule name that matches one row.

    Duplicate rule names (prohibitions.topic, manual_check) only resolve by index.
    An empty review list with no confirmations is valid.
    """
    checks = list((report or {}).get("checks") or []) if isinstance(report, dict) else []
    review = _review_indexes(checks)
    by_rule: dict[str, list[int]] = {}
    for i in review:
        by_rule.setdefault(str(checks[i].get("rule") or ""), []).append(i)
    chosen: set[int] = set()
    for item in confirmed or []:
        if isinstance(item, bool) or item is None:
            raise PublishGateError(f"confirmed_checks entry {item!r} is not an index or a rule name")
        if isinstance(item, int) or (isinstance(item, str) and item.lstrip("-").isdigit()):
            idx = int(item)
            if idx not in review:
                raise PublishGateError(f"confirmed check {idx} is not a review check")
            chosen.add(idx)
            continue
        if isinstance(item, str):
            hits = by_rule.get(item) or []
            if len(hits) == 1:
                chosen.add(hits[0])
                continue
            if not hits:
                raise PublishGateError(f"no review check named {item!r}")
            raise PublishGateError(f"rule {item!r} matches several review checks; confirm by index")
        raise PublishGateError(f"confirmed_checks entry {item!r} is not an index or a rule name")
    missing = [i for i in review if i not in chosen]
    if missing:
        names = ", ".join(str(checks[i].get("rule")) for i in missing)
        raise PublishGateError(f"unconfirmed review checks: {names}")
    return sorted(chosen)


def approve_clip_publish(
    db: Session,
    clip_id: uuid.UUID,
    platforms: Optional[list[str]] = None,
    confirmed_checks: Optional[list] = None,
    actor: str = "api:approve_publish",
) -> tuple[Clip, list[ClipPublication], bool]:
    wanted = list(platforms) if platforms else list(DEFAULT_PLATFORMS)
    wanted = [p.strip().lower() for p in wanted if p and p.strip()]
    if not wanted:
        wanted = list(DEFAULT_PLATFORMS)
    for p in wanted:
        if p not in SOCIAL_PLATFORM_VALUES:
            raise PublishGateError(
                f"platform must be one of {list(SOCIAL_PLATFORM_VALUES)}, got {p!r}"
            )

    clip = db.get(Clip, clip_id)
    if clip is None:
        raise PublishGateError(f"Clip {clip_id} not found")

    if clip.qa_status != "pass":
        raise PublishGateError(
            f"clip {clip_id} qa_status={clip.qa_status!r}, need 'pass'"
        )
    if clip.status != "approved":
        raise PublishGateError(
            f"clip {clip_id} status={clip.status!r}, need 'approved'"
        )
    if getattr(clip, "compliance_status", "pending") != "pass":
        raise PublishGateError(
            f"clip {clip_id} compliance_status={getattr(clip, 'compliance_status', None)!r}, need 'pass' (#43)"
        )
    if clip.location not in OK_LOCATIONS:
        raise PublishGateError(
            f"clip {clip_id} location={clip.location!r}, need pending_upload|uploaded"
        )

    already = clip.publish_approved_at is not None
    if not already:
        report = clip.compliance_report if isinstance(clip.compliance_report, dict) else {}
        chosen = require_review_confirmations(report, confirmed_checks)
        stored = dict(report)
        stored["human_confirmations"] = {"by": actor, "at": _now().isoformat(), "checks": chosen}
        clip.compliance_report = stored
        clip.publish_approved_at = _now()

    pubs: list[ClipPublication] = []
    for platform in wanted:
        existing = db.execute(
            select(ClipPublication).where(
                ClipPublication.clip_id == clip.id,
                ClipPublication.platform == platform,
            )
        ).scalar_one_or_none()
        if existing is None:
            existing = ClipPublication(
                clip_id=clip.id,
                platform=platform,
                status="pending",
                submit_status="pending",
            )
            db.add(existing)
        pubs.append(existing)

    db.commit()
    db.refresh(clip)
    for p in pubs:
        db.refresh(p)

    logger.info(
        "clip %s publish approved already=%s platforms=%s",
        clip.id, already, wanted,
    )
    return clip, pubs, already
