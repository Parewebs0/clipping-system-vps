"""Decide whether a campaign must be parked from its public detail (issue #23).

Pure functions (no DB, no network) so the tick and the tests share them.
"""
from __future__ import annotations

import os
from typing import Any

from app.services.discovery.whop_catalog import compute_economics, detail_summary


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def thresholds() -> dict[str, Any]:
    return {
        "max_spent_pct": _f("CLOSED_MAX_SPENT_PCT", 95.0),
        "min_remaining_usd": _f("CLOSED_MIN_REMAINING_USD", 250.0),
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
    if sp is not None and sp >= th["max_spent_pct"]:
        reasons.append(f"almost_exhausted:spent_{sp:.1f}%")
    rem = econ.get("remaining_usd")
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
