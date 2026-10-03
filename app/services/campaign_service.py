"""Campaign service — CRUD + manual state transitions (see campaign_transitions)."""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import bindparam, select, text
from sqlalchemy.orm import Session

from app.models.campaign import (
    CAMPAIGN_STATUS_VALUES,
    Campaign,
    CampaignSource,
    CampaignStatus,
)
from app.services.campaign_transitions import TransitionError, apply_transition
from app.schemas.campaign import CampaignCreate, CampaignUpdate

logger = logging.getLogger(__name__)


def create_campaign(db: Session, payload: CampaignCreate) -> Campaign:
    """Idempotent on name — raises ValueError if name exists."""
    existing = db.execute(
        select(Campaign).where(Campaign.name == payload.name)
    ).scalar_one_or_none()
    if existing is not None:
        raise ValueError(f"Campaign '{payload.name}' already exists")

    spec_dict = payload.spec.model_dump() if payload.spec else {}
    campaign = Campaign(
        name=payload.name,
        # Entry state of pipeline v2 (legacy 'draft' was dropped in 0012).
        status=CampaignStatus.DISCOVERED.value,
        source_provider=payload.source_provider,
        source_id=payload.source_id,
        source_url=payload.source_url,
        source_metadata=payload.source_metadata,
        source_instructions=payload.source_instructions,
        spec=spec_dict,
    )
    db.add(campaign)
    db.commit()
    db.refresh(campaign)
    logger.info(
        "campaign created: id=%s name=%s source=%s",
        campaign.id, campaign.name, campaign.source_provider,
    )
    return campaign


class CampaignConflict(ValueError):
    """409: duplicate name, forbidden transition or field not editable."""


class CampaignBadRequest(ValueError):
    """400: unknown status value."""


def update_campaign(
    db: Session, campaign_id: int, payload: CampaignUpdate, actor: str = "api"
) -> Optional[Campaign]:
    """Partial update. Returns None if campaign not found.

    Raises CampaignBadRequest (unknown status) or CampaignConflict (409).
    """
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        return None
    sent = payload.model_fields_set

    if payload.name is not None and payload.name != campaign.name:
        dup = db.execute(
            select(Campaign.id).where(Campaign.name == payload.name, Campaign.id != campaign.id)
        ).first()
        if dup is not None:
            raise CampaignConflict(f"Campaign '{payload.name}' already exists")
        campaign.name = payload.name

    for field in ("source_url", "source_id"):
        if field in sent and getattr(payload, field) != getattr(campaign, field):
            if campaign.source_provider != CampaignSource.MANUAL.value:
                raise CampaignConflict(
                    f"{field} is only editable on manual campaigns "
                    f"(for '{campaign.source_provider}' it is the discovery key)"
                )
            setattr(campaign, field, getattr(payload, field) or None)

    if "source_instructions" in sent:
        campaign.source_instructions = payload.source_instructions or None

    if payload.spec is not None:
        current = dict(campaign.spec or {})
        patch = payload.spec.model_dump(exclude_unset=True)
        extra_patch = patch.pop("extra", None)
        current.update(patch)
        if extra_patch is not None:
            current["extra"] = {**(current.get("extra") or {}), **extra_patch}
        dmin, dmax = current.get("duration_min"), current.get("duration_max")
        if dmin is not None and dmax is not None and dmin > dmax:
            raise CampaignBadRequest("duration_min must be <= duration_max")
        campaign.spec = current

    if payload.source_metadata is not None:
        # Merge on top of existing
        merged = {**(campaign.source_metadata or {}), **payload.source_metadata}
        campaign.source_metadata = merged

    if payload.status is not None:
        if payload.status not in CAMPAIGN_STATUS_VALUES:
            raise CampaignBadRequest(
                f"Invalid status '{payload.status}'. "
                f"Must be one of: {', '.join(CAMPAIGN_STATUS_VALUES)}"
            )
        try:
            apply_transition(campaign, payload.status, payload.status_reason, actor)
        except TransitionError as e:
            raise CampaignConflict(str(e)) from e

    db.commit()
    db.refresh(campaign)
    logger.info(
        "campaign updated: id=%s status=%s fields=%s",
        campaign.id, campaign.status, sorted(sent),
    )
    return campaign


def change_campaign_status(
    db: Session, campaign_id: int, target: str, reason: Optional[str] = None, actor: str = "api"
) -> Optional[Campaign]:
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        return None
    if target not in CAMPAIGN_STATUS_VALUES:
        raise CampaignBadRequest(
            f"Invalid status '{target}'. Must be one of: {', '.join(CAMPAIGN_STATUS_VALUES)}"
        )
    previous = campaign.status
    try:
        changed = apply_transition(campaign, target, reason, actor)
    except TransitionError as e:
        raise CampaignConflict(str(e)) from e
    if changed:
        db.commit()
        db.refresh(campaign)
        logger.info("campaign status: id=%s %s -> %s by=%s", campaign.id, previous, target, actor)
    return campaign


ACTIVE_JOB_STATUSES = ("pending", "assigned", "processing")


def delete_campaign(db: Session, campaign_id: int) -> Optional[bool]:
    """Hard delete (FK cascade: assets, candidates, clips, publications).

    Only archived campaigns without active jobs can be deleted.
    Returns None if not found. Raises CampaignConflict otherwise.
    """
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        return None
    if campaign.status != CampaignStatus.ARCHIVED.value:
        raise CampaignConflict("Only archived campaigns can be deleted; archive it first")
    active = db.execute(
        text(
            "SELECT COUNT(*) FROM jobs WHERE status IN :st "
            "AND payload->>'campaign_id' = :cid"
        ).bindparams(bindparam("st", expanding=True)),
        {"st": list(ACTIVE_JOB_STATUSES), "cid": str(campaign_id)},
    ).scalar_one()
    if active:
        raise CampaignConflict(f"Campaign has {active} active job(s); wait or cancel them first")
    name = campaign.name
    db.delete(campaign)
    db.commit()
    logger.info("campaign deleted: id=%s name=%s", campaign_id, name)
    return True


def get_campaign(db: Session, campaign_id: int) -> Optional[Campaign]:
    return db.get(Campaign, campaign_id)


def get_campaign_by_name(db: Session, name: str) -> Optional[Campaign]:
    return db.execute(
        select(Campaign).where(Campaign.name == name)
    ).scalar_one_or_none()


def list_campaigns(
    db: Session,
    status: Optional[str] = None,
    source_provider: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Campaign]:
    q = (
        select(Campaign)
        .order_by(Campaign.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if status is not None:
        q = q.where(Campaign.status == status)
    if source_provider is not None:
        q = q.where(Campaign.source_provider == source_provider)
    return list(db.execute(q).scalars())
