"""Deterministic campaign score. No LLM.

Issue #25 (P0-3 of audit_discovery_20261003.md):
  * money inputs = rate on the platform we publish to (DISCOVERY_PLATFORM,
    YouTube today) and the *remaining* budget, not the min CPM / total pool;
  * no penalty for asset count (the resolver caps at 12 anyway) nor for
    publication-copy rules (tags, post caption, FTC #ad) — those are text the
    publish step writes, not editing work;
  * penalties only for real production work / risk.

Score 0..100 = 100 × (0.45·rate_n + 0.25·remaining_n + 0.20·assets_n + 0.10·verified)
minus penalties, where rate_n = min(rate/2$, 1), remaining_n = min(rem/5k$, 1),
assets_n = min(real_assets/3, 1).
"""
from __future__ import annotations

import os
from typing import Any

MIN_SCORE_TO_RUN = 50.0
HEAVY_ONE_BYTES = 500 * 1024 * 1024
HEAVY_TOTAL_BYTES = 2 * 1024 * 1024 * 1024

RATE_FULL_USD = 2.0
REMAINING_FULL_USD = 5000.0
ASSETS_FULL = 3

W_RATE, W_REMAINING, W_ASSETS, W_VERIFIED = 45.0, 25.0, 20.0, 10.0


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def min_rate_usd() -> float:
    return _env_float("SCORE_MIN_RATE_USD", 0.5)


def target_platform() -> str:
    return (os.environ.get("DISCOVERY_PLATFORM", "youtube").strip().lower() or "youtube")


def difficulty_penalties(
    rules: dict[str, Any] | None,
    content_kinds: list[str] | None = None,
    *,
    real_assets: int = 0,  # kept for signature compatibility; no longer penalised
    file_sizes: list[int] | None = None,
) -> dict[str, float]:
    rules = rules or {}
    kinds = set(content_kinds or rules.get("content_source_kinds") or [])
    pen: dict[str, float] = {}
    if rules.get("watermark_required"):
        pen["watermark"] = 15.0
    if rules.get("on_screen_text_required"):
        pen["on_screen_text"] = 8.0
    if rules.get("extra_music_forbidden"):
        pen["no_extra_music"] = 2.0
    hard_hosts = {"mediasilo", "unsupported_host"}
    if kinds & hard_hosts or rules.get("unsupported_video_host"):
        pen["unsupported_host"] = 25.0
    sizes = [int(s) for s in (file_sizes or []) if s]
    if sizes:
        if max(sizes) >= HEAVY_ONE_BYTES:
            pen["heavy_one"] = 8.0
        if sum(sizes) >= HEAVY_TOTAL_BYTES:
            pen["heavy_total"] = 10.0
    elif rules.get("heavy_source_files"):
        pen["heavy_files"] = 10.0
    return pen


def base_score(real_assets: int, cpm: float, prize: float, verified: bool) -> float:
    """`cpm` = rate (USD/1k) on the publish platform; `prize` = remaining budget USD."""
    rate_n = min(max(cpm or 0.0, 0.0) / RATE_FULL_USD, 1.0)
    rem_n = min(max(prize or 0.0, 0.0) / REMAINING_FULL_USD, 1.0)
    assets_n = min(max(real_assets, 0) / ASSETS_FULL, 1.0)
    return W_RATE * rate_n + W_REMAINING * rem_n + W_ASSETS * assets_n + (W_VERIFIED if verified else 0.0)


def score_campaign(
    *,
    real_assets: int,
    cpm: float,
    prize: float,
    verified: bool,
    rules: dict[str, Any] | None = None,
    content_kinds: list[str] | None = None,
    file_sizes: list[int] | None = None,
) -> dict[str, Any]:
    raw = base_score(real_assets, cpm, prize, verified)
    pens = difficulty_penalties(rules, content_kinds, real_assets=real_assets, file_sizes=file_sizes)
    total_pen = sum(pens.values())
    value = round(min(100.0, max(0.0, raw - total_pen)), 2)
    floor = min_rate_usd()
    block_reason = None
    if real_assets <= 0:
        block_reason = "no_real_assets"
    elif "unsupported_host" in pens:
        block_reason = "unsupported_host"
    elif (cpm or 0.0) < floor:
        block_reason = f"rate_below_{floor:g}usd"
    elif value < MIN_SCORE_TO_RUN:
        block_reason = f"score_below_{MIN_SCORE_TO_RUN:g}"
    return {
        "value": value,
        "base": round(raw, 2),
        "penalties": pens,
        "penalty_total": total_pen,
        "min_to_run": MIN_SCORE_TO_RUN,
        "min_rate_usd": floor,
        "inputs": {"rate_usd": cpm, "remaining_usd": prize, "real_assets": real_assets, "verified": verified},
        "eligible": block_reason is None,
        "block_reason": block_reason,
    }


# --------------------------------------------------------------------------
# Money inputs from source_metadata
# --------------------------------------------------------------------------


def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def campaign_rate_usd(meta: dict | None, platform: str | None = None) -> float:
    """Rate (USD per 1k views) on `platform` (default DISCOVERY_PLATFORM).

    Order: economics.platform_rates (#21/#23) → discovered.payouts → 0.0.
    Deliberately does NOT fall back to cpm_usd_per_1k (min across platforms):
    a campaign that does not pay on our platform is worth 0 to us.
    """
    meta = meta or {}
    platform = (platform or target_platform()).lower()
    econ = meta.get("economics") or {}
    rates = econ.get("platform_rates") or {}
    r = _num((rates.get(platform) or {}).get("rate_usd"))
    if r is not None:
        return r
    discovered = meta.get("discovered") or {}
    best = None
    for p in discovered.get("payouts") or []:
        if isinstance(p, dict) and str(p.get("platform") or "").lower() == platform:
            v = _num(p.get("rate_cents"))
            if v is not None:
                best = max(best or 0.0, v / 100.0)
    return best or 0.0


def campaign_remaining_usd(meta: dict | None) -> float:
    """Remaining budget USD: economics → discovered.raw.metrics → prize pool."""
    meta = meta or {}
    rem = _num((meta.get("economics") or {}).get("remaining_usd"))
    if rem is not None:
        return max(rem, 0.0)
    discovered = meta.get("discovered") or {}
    metrics = (discovered.get("raw") or {}).get("metrics") or {}
    pool = _num(discovered.get("prize_pool_usd"))
    spent = _num(metrics.get("budgetSpentCents"))
    if pool is not None and spent is not None:
        return max(pool - spent / 100.0, 0.0)
    return max(pool or 0.0, 0.0)
