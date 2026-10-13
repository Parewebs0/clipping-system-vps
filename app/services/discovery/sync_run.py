"""Whop discovery pass (issue #72).

Refresh every non-archived campaign, queue new ones with no slot cap, then
activate the highest-scored queued campaigns into `discovered` while the
number of pipeline campaigns is under DISCOVERY_MAX_ACTIVE.

`active_statuses` is the pipeline (discovered → scored). `needs_review` is
not part of it, so a campaign waiting on a person does not block the next one.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.models.campaign import Campaign, CampaignStatus
from app.services.campaign_closed import append_budget_point, sync_whop_campaign
from app.services.campaign_transitions import record_auto_transition
from app.services.discovery.whop_catalog import CatalogClient, CatalogFetchError


def _count(db, statuses: tuple[str, ...]) -> int:
    return db.query(Campaign).filter(Campaign.status.in_(statuses)).count()


def _queue_score(campaign) -> float:
    meta = campaign.source_metadata or {}
    raw = meta.get("queue_score")
    if raw is None:
        raw = (meta.get("economics") or {}).get("expected_value")
    try:
        return float(raw) if raw is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def candidate_to_discovered(cand):
    """Listing card + economics + detail → DiscoveredCampaign."""
    from app.services.discovery.models import DiscoveredCampaign
    from app.services.discovery.providers.whop import WhopProvider
    from app.services.discovery.whop_catalog import detail_summary

    struct = WhopProvider("https://contentrewards.com")._api_card_to_struct(cand.card)
    if struct is None:
        return None
    econ = dict(cand.economics)
    econ["expected_value"] = cand.ev
    detail = detail_summary(cand.detail) if cand.detail else {}
    return DiscoveredCampaign(
        provider="whop",
        external_id=struct["external_id"],
        detail_url=struct["detail_url"],
        name=struct["name"],
        description=struct.get("description"),
        cpm_usd_per_1k=struct.get("cpm_usd_per_1k"),
        prize_pool_usd=struct.get("prize_pool_usd"),
        joined=struct.get("joined"),
        asset_links=struct.get("asset_links", []),
        payouts=struct.get("payouts", []),
        reference_materials=struct.get("reference_materials", []),
        organization_name=struct.get("organization_name"),
        organization_verified=struct.get("organization_verified"),
        organization_id=struct.get("organization_id"),
        categories=struct.get("categories", []),
        platforms=struct.get("platforms", []),
        status=struct.get("status"),
        requires_application=struct.get("requires_application"),
        primary_payout_cents=struct.get("primary_payout_cents"),
        raw={**struct.get("raw", {}), "fetch_failed": False},
        economics=econ,
        detail=detail,
    )


def _detail_url(card: dict) -> str:
    return (
        f"https://whop.com/experiences/{card.get('organizationExperienceId') or ''}"
        f"/campaigns/{card.get('id') or ''}"
    )


def _already_known(db, detail_url: str) -> bool:
    if not detail_url:
        return False
    return db.query(Campaign.id).filter(Campaign.source_url == detail_url).first() is not None


def _refresh(db, client, *, dry_run: bool) -> dict[str, int]:
    rows = (
        db.query(Campaign)
        .filter(Campaign.source_provider == "whop")
        .filter(Campaign.status != CampaignStatus.ARCHIVED.value)
        .order_by(Campaign.id.asc())
        .all()
    )
    counts = {"refreshed": 0, "archived": 0, "parked": 0, "refresh_errors": 0}
    for campaign in rows:
        entry = sync_whop_campaign(
            db,
            campaign,
            client,
            dry_run=dry_run,
            check_fit=True,
            actor="whop_discovery",
        )
        outcome = entry.get("outcome")
        if outcome == "error":
            counts["refresh_errors"] += 1
        elif outcome in ("kept", "parked", "archived"):
            counts["refreshed"] += 1
            if entry.get("changed") and outcome in ("parked", "archived"):
                counts[outcome] += 1
    return counts


def _remember_new(db, campaign, cand, now: datetime) -> None:
    """Stamp the discovery score and the first budget point on a new row."""
    meta = dict(campaign.source_metadata or {})
    meta["queue_score"] = cand.ev
    econ = dict(meta.get("economics") or {})
    point = {
        "at": now.isoformat(),
        "remaining_usd": econ.get("remaining_usd"),
        "cpm_usd": econ.get("target_rate_usd"),
        "spent_pct": econ.get("spent_pct"),
        "whop_status": (meta.get("detail") or {}).get("status") or meta.get("status"),
    }
    campaign.source_metadata = meta
    campaign.last_synced_at = now
    campaign.budget_history = append_budget_point(campaign.budget_history, point)
    db.commit()


def _activate(db, slots: int) -> int:
    if slots <= 0:
        return 0
    queued = (
        db.query(Campaign)
        .filter(Campaign.status == CampaignStatus.QUEUED.value)
        .all()
    )
    queued.sort(key=lambda c: (-_queue_score(c), c.id))
    activated = 0
    for campaign in queued:
        if activated >= slots:
            break
        db.refresh(campaign, with_for_update=True)
        if campaign.status != CampaignStatus.QUEUED.value:
            db.commit()
            continue
        record_auto_transition(
            campaign,
            CampaignStatus.DISCOVERED.value,
            reason=f"activated score={_queue_score(campaign):.2f}",
            actor="whop_discovery",
        )
        db.commit()
        activated += 1
    return activated


def execute(args, db, client=None, *, active_statuses: tuple[str, ...]) -> tuple[int, dict]:
    from app.services.discovery.upsert import upsert_campaign
    from app.services.discovery.whop_catalog import DiscoveryFilters, select_candidates

    filters = DiscoveryFilters.from_env()
    if getattr(args, "shortlist", None) is not None:
        filters.shortlist_size = args.shortlist
    summary: dict[str, Any] = {
        "provider": "whop",
        "catalog": 0,
        "filtered": 0,
        "shortlist": 0,
        "accepted": 0,
        "upserted": 0,
        "entered": 0,
        "refreshed": 0,
        "archived": 0,
        "parked": 0,
        "activated": 0,
        "refresh_errors": 0,
        "skipped_full": 0,
        "active": 0,
        "slots": 0,
        "campaigns": [],
        "dry_run": args.dry_run,
        "max_active": args.max_active,
    }
    client = client or CatalogClient()
    now = datetime.now(timezone.utc)
    summary.update(_refresh(db, client, dry_run=args.dry_run))
    active = _count(db, active_statuses)
    slots = max(0, args.max_active - active)
    summary["active"] = active
    summary["slots"] = slots

    try:
        accepted, stats = select_candidates(
            client,
            filters,
            is_known=lambda url: _already_known(db, url),
            detail_url=_detail_url,
            want=None,
        )
    except CatalogFetchError as e:
        summary["error"] = f"catalog_fetch_failed: {e}"
        summary["requests"] = client.requests_made
        summary["log"] = _log_line(summary)
        return 2, summary
    summary.update({k: v for k, v in stats.items() if k != "filters"})
    summary["filters"] = stats.get("filters")

    if args.dry_run:
        summary["campaigns"] = [
            {
                "name": c.card.get("name"),
                "ev": c.ev,
                "youtube_rate_usd": c.economics.get("target_rate_usd"),
                "remaining_usd": c.economics.get("remaining_usd"),
                "spent_pct": c.economics.get("spent_pct"),
                "creators": c.economics.get("creators"),
                "detail_url": _detail_url(c.card),
            }
            for c in accepted
        ]
        waiting = db.query(Campaign).filter(Campaign.status == CampaignStatus.QUEUED.value).count()
        summary["would_enter"] = len(accepted)
        summary["would_activate"] = min(slots, waiting + len(accepted))
        summary["requests"] = client.requests_made
        summary["log"] = _log_line(summary)
        return 0, summary

    entered = 0
    for cand in accepted:
        discovered = candidate_to_discovered(cand)
        if discovered is None:
            continue
        campaign = upsert_campaign(db, discovered, status=CampaignStatus.QUEUED.value)
        _remember_new(db, campaign, cand, now)
        entered += 1
        summary["campaigns"].append(
            {
                "id": campaign.id,
                "name": campaign.name,
                "status": campaign.status,
                "youtube_rate_usd": cand.economics.get("target_rate_usd"),
                "remaining_usd": cand.economics.get("remaining_usd"),
                "ev": cand.ev,
            }
        )
    summary["entered"] = entered
    summary["upserted"] = entered
    # Re-count: a refresh may have freed a slot. New rows are queued, so they
    # do not change this number.
    active = _count(db, active_statuses)
    slots = max(0, args.max_active - active)
    activated = _activate(db, slots)
    for item in summary["campaigns"]:
        row = db.get(Campaign, item["id"])
        if row is not None:
            item["status"] = row.status
    summary["activated"] = activated
    summary["active"] = _count(db, active_statuses)
    summary["slots"] = max(0, args.max_active - summary["active"])
    summary["requests"] = client.requests_made
    summary["log"] = _log_line(summary)
    return 0, summary


def _log_line(summary: dict) -> str:
    return (
        "discovery "
        f"refreshed={summary.get('refreshed', 0)} "
        f"entered={summary.get('entered', 0)} "
        f"archived={summary.get('archived', 0)} "
        f"parked={summary.get('parked', 0)} "
        f"activated={summary.get('activated', 0)} "
        f"active={summary.get('active', 0)} "
        f"slots={summary.get('slots', 0)}"
    )
