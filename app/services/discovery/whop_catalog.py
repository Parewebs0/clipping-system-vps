"""Content Rewards (Whop) catalog client + hard filters + expected-value ranking.

Issue #21 (P0-1 of audit_discovery_20261003.md).

Endpoints (public, unauthenticated JSON used by the contentrewards.com SPA):

    GET {tenant}/api/campaign/campaigns/discover?limit=50&sortBy=...&cursor=...
        -> {"data": [...], "pagination": {"nextCursor": ...}, "success": true}
    GET {tenant}/api/campaign/campaigns/{id}
        -> {"data": {..., "configuration": {...}}, "success": true}

They are NOT an official Whop API (robots.txt disallows /api/), so we keep the
volume low: ~15 list pages + a short list of detail calls per run, a pause
between requests, an identifiable User-Agent, timeouts and a bounded retry
with exponential backoff. A network/DNS failure raises `CatalogFetchError`
so the cron exits non-zero instead of logging "0 campaigns found".
"""
from __future__ import annotations

import json
import logging
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger(__name__)

DEFAULT_TENANT = "https://contentrewards.com"
DEFAULT_UA = (
    "clipping-system-vps/1.0 (campaign discovery; low-volume; "
    "+https://github.com/Parewebs0/clipping-system-vps)"
)


class CatalogFetchError(RuntimeError):
    """Raised when the catalog/detail endpoint cannot be fetched after retries."""


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


# --------------------------------------------------------------------------
# HTTP client
# --------------------------------------------------------------------------


class CatalogClient:
    """Tiny polite HTTP client for the Content Rewards JSON API."""

    def __init__(
        self,
        tenant_url: str | None = None,
        *,
        user_agent: str | None = None,
        timeout_s: float | None = None,
        retries: int | None = None,
        backoff_s: float | None = None,
        pause_s: float | None = None,
        opener: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.tenant_url = (tenant_url or os.environ.get("WHOP_TENANT_URL") or DEFAULT_TENANT).rstrip("/")
        self.user_agent = user_agent or os.environ.get("DISCOVERY_USER_AGENT") or DEFAULT_UA
        self.timeout_s = timeout_s if timeout_s is not None else _env_float("WHOP_API_TIMEOUT_S", 15.0)
        self.retries = retries if retries is not None else _env_int("DISCOVERY_HTTP_RETRIES", 3)
        self.backoff_s = backoff_s if backoff_s is not None else _env_float("DISCOVERY_HTTP_BACKOFF_S", 5.0)
        self.pause_s = pause_s if pause_s is not None else _env_float("DISCOVERY_PAUSE_S", 1.5)
        self._open = opener or urllib.request.urlopen
        self._sleep = sleep
        self._last_request_at: float | None = None
        self.requests_made = 0

    def _get_json(self, url: str) -> dict:
        last_err: Exception | None = None
        for attempt in range(self.retries + 1):
            if self._last_request_at is not None and self.pause_s > 0:
                self._sleep(self.pause_s)
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": self.user_agent,
                    "Accept": "application/json",
                    "Referer": f"{self.tenant_url}/discover",
                },
            )
            try:
                self.requests_made += 1
                self._last_request_at = time.monotonic()
                with self._open(req, timeout=self.timeout_s) as r:
                    payload = json.loads(r.read().decode("utf-8", errors="replace"))
                if not isinstance(payload, dict) or payload.get("success") is not True:
                    raise CatalogFetchError(f"unexpected_payload: {str(payload)[:200]}")
                return payload
            except urllib.error.HTTPError as e:
                last_err = e
                # 4xx other than 429 will not get better by retrying.
                if 400 <= e.code < 500 and e.code != 429:
                    raise CatalogFetchError(f"HTTP {e.code} for {url}") from e
            except (urllib.error.URLError, OSError, TimeoutError, json.JSONDecodeError) as e:
                last_err = e
            if attempt < self.retries:
                wait = self.backoff_s * (2 ** attempt)
                logger.warning("catalog GET failed (%s), retry %d/%d in %.1fs", last_err, attempt + 1, self.retries, wait)
                self._sleep(wait)
        raise CatalogFetchError(f"{type(last_err).__name__}: {last_err}")

    def fetch_catalog(self, *, sort_by: str = "trending", page_size: int = 50, max_pages: int | None = None) -> list[dict]:
        """Walk every page of the discover listing. Raises CatalogFetchError."""
        max_pages = max_pages if max_pages is not None else _env_int("DISCOVERY_MAX_PAGES", 30)
        out: list[dict] = []
        seen: set[str] = set()
        cursor: str | None = None
        for _ in range(max_pages):
            params = {"limit": page_size, "sortBy": sort_by}
            if cursor:
                params["cursor"] = cursor
            url = f"{self.tenant_url}/api/campaign/campaigns/discover?{urllib.parse.urlencode(params)}"
            payload = self._get_json(url)
            data = payload.get("data")
            if not isinstance(data, list):
                raise CatalogFetchError("data_not_list")
            for c in data:
                if isinstance(c, dict) and c.get("id") and c["id"] not in seen:
                    seen.add(c["id"])
                    out.append(c)
            cursor = (payload.get("pagination") or {}).get("nextCursor")
            if not cursor or not data:
                break
        return out

    def fetch_detail(self, campaign_id: str) -> dict:
        url = f"{self.tenant_url}/api/campaign/campaigns/{urllib.parse.quote(campaign_id)}"
        data = self._get_json(url).get("data")
        if not isinstance(data, dict):
            raise CatalogFetchError("detail_not_dict")
        return data


# --------------------------------------------------------------------------
# Economics + filters + ranking
# --------------------------------------------------------------------------


def _cents(v: Any) -> float | None:
    try:
        return float(v) / 100.0 if v is not None else None
    except (TypeError, ValueError):
        return None


def _parse_dt(s: Any) -> datetime | None:
    if not isinstance(s, str) or not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def platform_rates(card: dict) -> dict[str, dict[str, float | None]]:
    """{platform: {rate_usd, min_payout_usd, max_payout_usd}} from payouts[]/payoutModel[]."""
    out: dict[str, dict[str, float | None]] = {}
    rows = card.get("payouts")
    if not rows:
        rows = ((card.get("configuration") or {}).get("payoutModel")) or []
    for p in rows or []:
        if not isinstance(p, dict) or not p.get("platform"):
            continue
        rate = p.get("rateCents", p.get("rate"))
        mn = p.get("minPayoutCents", p.get("minPayout"))
        mx = p.get("maxPayoutCents", p.get("maxPayout"))
        out[str(p["platform"])] = {
            "rate_usd": _cents(rate),
            "min_payout_usd": _cents(mn),
            "max_payout_usd": _cents(mx),
        }
    return out


def compute_economics(card: dict, *, now: datetime | None = None, platform: str = "youtube") -> dict[str, Any]:
    """Derive the money fields we rank and filter on from a listing card."""
    now = now or datetime.now(timezone.utc)
    m = card.get("metrics") or {}
    budget = _cents(card.get("budgetCents"))
    if budget is None:
        budget = _cents((card.get("configuration") or {}).get("budget"))
    spent = _cents(m.get("budgetSpentCents")) or 0.0
    bps = m.get("budgetProgressBps")
    if isinstance(bps, (int, float)):
        spent_pct = float(bps) / 100.0
    elif budget:
        spent_pct = 100.0 * spent / budget
    else:
        spent_pct = None
    remaining = max(0.0, (budget or 0.0) - spent) if budget is not None else None
    rates = platform_rates(card)
    target_rate = (rates.get(platform) or {}).get("rate_usd")
    started = _parse_dt(card.get("launchDate")) or _parse_dt(card.get("createdAt"))
    burn_per_day = runway_days = None
    if started and spent > 0:
        days = max((now - started).total_seconds() / 86400.0, 0.5)
        burn_per_day = spent / days
        if remaining is not None and burn_per_day > 0:
            runway_days = remaining / burn_per_day
    return {
        "budget_usd": budget,
        "spent_usd": round(spent, 2),
        "remaining_usd": round(remaining, 2) if remaining is not None else None,
        "spent_pct": round(spent_pct, 2) if spent_pct is not None else None,
        "platform_rates": rates,
        "target_platform": platform,
        "target_rate_usd": target_rate,
        "creators": m.get("creatorCount"),
        "approved_submissions": m.get("approvedSubmissionCount"),
        "total_views": m.get("totalViews"),
        "burn_usd_per_day": round(burn_per_day, 2) if burn_per_day is not None else None,
        "runway_days": round(runway_days, 2) if runway_days is not None else None,
        "requires_application": card.get("requiresApplication"),
        "payout_type": card.get("payoutType"),
        # Detail payloads have no top-level `platforms`; fall back to payoutModel.
        "platforms": list(card.get("platforms") or rates.keys()),
    }


@dataclass
class DiscoveryFilters:
    platform: str = "youtube"
    min_rate_usd: float = 1.0
    min_remaining_usd: float = 1500.0
    max_spent_pct: float = 85.0
    allow_application: bool = False
    required_content_type: str = "clipping"
    shortlist_size: int = 20

    @classmethod
    def from_env(cls) -> "DiscoveryFilters":
        return cls(
            platform=os.environ.get("DISCOVERY_PLATFORM", "youtube").strip().lower() or "youtube",
            min_rate_usd=_env_float("DISCOVERY_MIN_RATE_USD", 1.0),
            min_remaining_usd=_env_float("DISCOVERY_MIN_REMAINING_USD", 1500.0),
            max_spent_pct=_env_float("DISCOVERY_MAX_SPENT_PCT", 85.0),
            allow_application=_env_bool("DISCOVERY_ALLOW_APPLICATION", False),
            required_content_type=os.environ.get("DISCOVERY_CONTENT_TYPE", "clipping").strip().lower(),
            shortlist_size=_env_int("DISCOVERY_SHORTLIST", 20),
        )

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def hard_filter_reason(card: dict, econ: dict, f: DiscoveryFilters) -> str | None:
    """Return None if the listing card passes, else a short rejection reason."""
    if card.get("status") not in (None, "active"):
        return "status_not_active"
    if card.get("showOnDiscover") is False:
        return "hidden"
    if (card.get("payoutType") or "cpm") != "cpm":
        return "payout_not_cpm"
    if f.platform and f.platform not in [p.lower() for p in econ.get("platforms") or []]:
        return f"no_{f.platform}"
    rate = econ.get("target_rate_usd")
    if rate is None or rate < f.min_rate_usd:
        return "rate_too_low"
    rem = econ.get("remaining_usd")
    if rem is None or rem < f.min_remaining_usd:
        return "remaining_too_low"
    sp = econ.get("spent_pct")
    if sp is not None and sp >= f.max_spent_pct:
        return "spent_too_high"
    if econ.get("requires_application") and not f.allow_application:
        return "requires_application"
    return None


def expected_value(econ: dict) -> float:
    """Rank key: target rate × remaining budget, damped by competition and burn.

    * competition: divide by sqrt(1 + creators/25) (25 creators ≈ ×0.71).
    * burn: campaigns with < 7 days of runway at the current spend rate are
      scaled by runway/7 (we need days, not hours, to publish and accrue views).
    """
    rate = float(econ.get("target_rate_usd") or 0.0)
    rem = float(econ.get("remaining_usd") or 0.0)
    ev = rate * rem
    creators = econ.get("creators")
    if isinstance(creators, (int, float)) and creators > 0:
        ev /= math.sqrt(1.0 + float(creators) / 25.0)
    runway = econ.get("runway_days")
    if isinstance(runway, (int, float)) and runway < 7.0:
        ev *= max(runway, 0.0) / 7.0
    return round(ev, 2)


def detail_summary(detail: dict) -> dict[str, Any]:
    """The useful bits of the detail payload, stored in source_metadata.detail."""
    cf = detail.get("configuration") or {}
    content = cf.get("content") or {}
    creator = cf.get("creator") or {}
    return {
        "status": detail.get("status"),
        "status_reason": detail.get("statusReason"),
        "status_changed_at": detail.get("statusChangedAt"),
        "show_on_discover": detail.get("showOnDiscover"),
        "content_types": [t.get("id") for t in content.get("contentTypes") or [] if isinstance(t, dict)],
        "content_categories": [t.get("id") for t in content.get("contentCategories") or [] if isinstance(t, dict)],
        "guidelines": (content.get("guidelines") or None),
        "creator_description": (creator.get("description") or None),
        "requirement": cf.get("requirement") or {},
        "rules": cf.get("rules") or {},
        "reference_material": cf.get("referenceMaterial") or [],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def detail_reject_reason(detail: dict, f: DiscoveryFilters) -> str | None:
    s = detail_summary(detail)
    if s["status"] != "active":
        return f"detail_status_{s['status']}"
    if s["show_on_discover"] is False:
        return "detail_hidden"
    if f.required_content_type and f.required_content_type not in [str(t).lower() for t in s["content_types"]]:
        return "not_" + f.required_content_type
    if detail.get("requiresApplication") and not f.allow_application:
        return "requires_application"
    return None


@dataclass
class Candidate:
    card: dict
    economics: dict
    ev: float
    detail: dict | None = None
    reject: str | None = None
    extra: dict = field(default_factory=dict)


def select_candidates(
    client: CatalogClient,
    filters: DiscoveryFilters,
    *,
    is_known: Callable[[str], bool] = lambda url: False,
    detail_url: Callable[[dict], str] | None = None,
    now: datetime | None = None,
    want: int | None = None,
) -> tuple[list[Candidate], dict[str, Any]]:
    """Catalog → hard filter → rank → detail for the short list.

    Returns (accepted candidates ranked by EV, stats). Known campaigns (already
    in DB) are skipped *before* the detail call so they do not burn requests.
    Detail calls stop once `want` candidates are accepted or after
    `filters.shortlist_size` detail requests.
    """
    cards = client.fetch_catalog()
    stats: dict[str, Any] = {"catalog": len(cards), "rejected": {}, "filters": filters.as_dict()}
    passed: list[Candidate] = []
    for c in cards:
        econ = compute_economics(c, now=now, platform=filters.platform)
        reason = hard_filter_reason(c, econ, filters)
        if reason:
            stats["rejected"][reason] = stats["rejected"].get(reason, 0) + 1
            continue
        passed.append(Candidate(card=c, economics=econ, ev=expected_value(econ)))
    passed.sort(key=lambda x: x.ev, reverse=True)
    stats["filtered"] = len(passed)

    durl = detail_url or (lambda c: c.get("id", ""))
    accepted: list[Candidate] = []
    detail_rejected: dict[str, int] = {}
    known = 0
    detail_calls = 0
    for cand in passed:
        if want is not None and len(accepted) >= want:
            break
        if detail_calls >= filters.shortlist_size:
            break
        if is_known(durl(cand.card)):
            known += 1
            continue
        detail_calls += 1
        try:
            cand.detail = client.fetch_detail(cand.card["id"])
        except CatalogFetchError as e:
            cand.reject = "detail_fetch_failed"
            detail_rejected["detail_fetch_failed"] = detail_rejected.get("detail_fetch_failed", 0) + 1
            logger.warning("detail fetch failed for %s: %s", cand.card.get("id"), e)
            continue
        reason = detail_reject_reason(cand.detail, filters)
        if reason:
            cand.reject = reason
            detail_rejected[reason] = detail_rejected.get(reason, 0) + 1
            continue
        accepted.append(cand)
    stats["known_skipped"] = known
    stats["shortlist"] = detail_calls
    stats["detail_rejected"] = detail_rejected
    stats["accepted"] = len(accepted)
    stats["requests"] = client.requests_made
    return accepted, stats
