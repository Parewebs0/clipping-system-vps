"""Decide whether a Whop campaign stays, parks, or leaves the pipeline (issues #23, #72).

Pure helpers plus `sync_whop_campaign`, which refreshes money fields and may
change status. It never cancels jobs: in-progress renders keep running, and
callers simply stop enqueueing new work once the campaign is no longer workable.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from app.services.discovery.whop_catalog import compute_economics, detail_summary, expected_value

HISTORY_MAX = 60


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_set(name: str) -> bool:
    return os.environ.get(name) not in (None, "")


def thresholds() -> dict[str, Any]:
    """Park when remaining money is under MIN_REMAINING_BUDGET_USD (default 500)
    or the remaining percent is under MIN_REMAINING_BUDGET_PCT (default 10).

    CLOSED_MIN_REMAINING_USD / CLOSED_MAX_SPENT_PCT still apply when the new
    names are unset, so an existing host config keeps its numbers.
    """
    if _env_set("MIN_REMAINING_BUDGET_USD"):
        min_usd = _f("MIN_REMAINING_BUDGET_USD", 500.0)
    elif _env_set("CLOSED_MIN_REMAINING_USD"):
        min_usd = _f("CLOSED_MIN_REMAINING_USD", 500.0)
    else:
        min_usd = 500.0
    if _env_set("MIN_REMAINING_BUDGET_PCT"):
        min_pct = _f("MIN_REMAINING_BUDGET_PCT", 10.0)
    elif _env_set("CLOSED_MAX_SPENT_PCT"):
        min_pct = max(0.0, 100.0 - _f("CLOSED_MAX_SPENT_PCT", 90.0))
    else:
        min_pct = 10.0
    return {
        "min_remaining_usd": min_usd,
        "min_remaining_pct": min_pct,
        "max_spent_pct": round(100.0 - min_pct, 4),
        "platform": (os.environ.get("DISCOVERY_PLATFORM", "youtube").strip().lower() or "youtube"),
        "content_type": os.environ.get("DISCOVERY_CONTENT_TYPE", "clipping").strip().lower(),
    }


def campaign_uuid_from_url(source_url: str | None) -> str | None:
    if not source_url or "/campaigns/" not in source_url:
        return None
    tail = source_url.rstrip("/").rsplit("/campaigns/", 1)[-1]
    return tail.split("/", 1)[0].split("?", 1)[0] or None


def park_reasons(detail: dict, *, check_fit: bool = True, th: dict | None = None) -> tuple[list[str], dict, dict]:
    """Return (reasons, economics, detail_summary). Empty reasons = keep."""
    th = th or thresholds()
    econ = compute_economics(detail, platform=th["platform"])
    summ = detail_summary(detail)
    reasons: list[str] = []
    status = summ.get("status")
    if status != "active":
        reasons.append(f"closed:{status}" + (f"/{summ.get('status_reason')}" if summ.get("status_reason") else ""))
    if summ.get("show_on_discover") is False:
        reasons.append("hidden_from_discover")
    sp = econ.get("spent_pct")
    rem = econ.get("remaining_usd")
    budget = econ.get("budget_usd")
    rem_pct = None
    if rem is not None and budget:
        rem_pct = 100.0 * float(rem) / float(budget)
    elif sp is not None:
        rem_pct = 100.0 - float(sp)
    # Empty budget leaves the pipeline. A low-but-positive balance parks.
    # Spent-% stays in this order so older callers that only set max_spent_pct
    # still see spent reason, then remaining reason.
    min_pct = th.get("min_remaining_pct")
    if rem is not None and rem <= 0:
        reasons.append("budget_empty")
    else:
        if min_pct is not None and rem_pct is not None and rem_pct < float(min_pct):
            reasons.append(f"below_remaining_pct:{rem_pct:.1f}%")
        elif sp is not None and sp >= th["max_spent_pct"]:
            reasons.append(f"almost_exhausted:spent_{sp:.1f}%")
        if rem is not None and rem < th["min_remaining_usd"]:
            reasons.append(f"almost_exhausted:remaining_{rem:.0f}usd")
    if check_fit:
        types = [str(t).lower() for t in summ.get("content_types") or []]
        if th["content_type"] and th["content_type"] not in types:
            reasons.append(f"not_{th['content_type']}:{','.join(types) or 'none'}")
        if th["platform"] and th["platform"] not in [p.lower() for p in econ.get("platforms") or []]:
            reasons.append(f"no_{th['platform']}")
        if detail.get("requiresApplication"):
            reasons.append("requires_application")
    return reasons, econ, summ


def disposition(reasons: list[str], econ: dict | None = None) -> str:
    """`archived` when Whop closed it, it disappeared, or the budget is empty.

    Anything else with a reason (low remaining money, unfit, hidden) is `parked`.
    No reasons → `kept`.
    """
    econ = econ or {}
    rem = econ.get("remaining_usd")
    for reason in reasons:
        if reason == "not_found_on_source" or reason == "budget_empty" or str(reason).startswith("closed:"):
            return "archived"
    if rem is not None and rem <= 0:
        return "archived"
    if reasons:
        return "parked"
    return "kept"


def append_budget_point(history: list | None, point: dict) -> list:
    """Keep one point per UTC day while the remaining budget is unchanged.

    A change in remaining (or Whop status) always appends, including later
    the same day. The series is capped so the column stays small.
    """
    history = list(history or [])
    last = history[-1] if history else None
    if last is not None:
        same_money = last.get("remaining_usd") == point.get("remaining_usd")
        same_status = last.get("whop_status") == point.get("whop_status")
        same_day = str(last.get("at") or "")[:10] == str(point.get("at") or "")[:10]
        if same_money and same_status and same_day:
            return history
    history.append(point)
    return history[-HISTORY_MAX:]


def _budget_point(now: datetime, econ: dict, summ: dict) -> dict:
    return {
        "at": now.isoformat(),
        "remaining_usd": econ.get("remaining_usd"),
        "cpm_usd": econ.get("target_rate_usd"),
        "spent_pct": econ.get("spent_pct"),
        "whop_status": summ.get("status"),
    }


def apply_refresh(campaign, reasons: list[str], econ: dict, summ: dict, *, now: datetime, actor: str) -> str:
    """Write the sync onto `campaign` (no commit, no job changes).

    Returns kept / parked / archived.
    """
    from app.services.campaign_transitions import record_auto_transition

    outcome = disposition(reasons, econ)
    now_iso = now.isoformat()
    meta = dict(campaign.source_metadata or {})
    if econ:
        meta["economics"] = {**(meta.get("economics") or {}), **econ}
        if econ.get("target_rate_usd") is not None:
            meta["cpm_usd_per_1k"] = econ["target_rate_usd"]
        if econ.get("budget_usd") is not None:
            meta["prize_pool_usd"] = econ["budget_usd"]
        if econ.get("target_rate_usd") is not None and econ.get("remaining_usd") is not None:
            meta["queue_score"] = expected_value(econ)
    if summ:
        meta["detail"] = {**(meta.get("detail") or {}), **summ}
        if summ.get("status"):
            meta["status"] = summ["status"]
    meta["closed_check"] = {"at": now_iso, "reasons": reasons}
    if outcome == "parked":
        meta["park"] = {"at": now_iso, "reasons": reasons, "previous_status": campaign.status}
    elif outcome == "archived":
        meta["archive"] = {"at": now_iso, "reasons": reasons, "previous_status": campaign.status}
    campaign.source_metadata = meta
    campaign.budget_history = append_budget_point(
        campaign.budget_history, _budget_point(now, econ, summ)
    )
    campaign.last_synced_at = now
    if outcome in ("parked", "archived"):
        record_auto_transition(campaign, outcome, reason="; ".join(reasons), actor=actor)
    return outcome


def sync_whop_campaign(
    db,
    campaign,
    client,
    *,
    dry_run: bool,
    check_fit: bool,
    actor: str,
    th: dict | None = None,
    now: datetime | None = None,
) -> dict:
    """Read one Whop detail, refresh the row, maybe park or archive it.

    Does not cancel jobs. A campaign already archived is left untouched.
    """
    from app.models.campaign import CampaignStatus
    from app.services.discovery.whop_catalog import CatalogFetchError

    th = th or thresholds()
    now = now or datetime.now(timezone.utc)
    base = {
        "id": campaign.id,
        "status": campaign.status,
        "reasons": [],
        "spent_pct": None,
        "remaining_usd": None,
        "youtube_rate_usd": None,
    }
    if campaign.status == CampaignStatus.ARCHIVED.value:
        return {**base, "outcome": "skipped", "skipped": "already_archived"}
    uid = campaign_uuid_from_url(campaign.source_url)
    if not uid:
        return {**base, "outcome": "error", "error": "no_uuid_in_source_url"}
    try:
        detail = client.fetch_detail(uid)
        reasons, econ, summ = park_reasons(detail, check_fit=check_fit, th=th)
    except CatalogFetchError as e:
        if "HTTP 404" in str(e):
            reasons, econ, summ = ["not_found_on_source"], {}, {}
        else:
            return {**base, "outcome": "error", "error": str(e)[:200]}
    outcome = disposition(reasons, econ)
    entry = {
        **base,
        "outcome": outcome,
        "reasons": reasons,
        "spent_pct": econ.get("spent_pct"),
        "remaining_usd": econ.get("remaining_usd"),
        "youtube_rate_usd": econ.get("target_rate_usd"),
        "changed": outcome in ("parked", "archived") and campaign.status != outcome,
    }
    if dry_run:
        return entry
    db.refresh(campaign, with_for_update=True)
    if campaign.status == CampaignStatus.ARCHIVED.value:
        db.commit()
        entry["outcome"] = "skipped"
        entry["changed"] = False
        entry["skipped"] = "already_archived"
        return entry
    before = campaign.status
    apply_refresh(campaign, reasons, econ, summ, now=now, actor=actor)
    entry["changed"] = campaign.status != before
    db.commit()
    return entry
