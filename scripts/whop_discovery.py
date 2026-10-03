#!/usr/bin/env python3
"""Paso 0 — Whop / Content Rewards discovery (issue #21).

Catálogo completo paginado → filtros duros (env) → ranking por valor esperado
→ detalle solo para la lista corta (clipping + active) → inserta solo si hay
hueco bajo MAX_ACTIVE.

Activas = discovered | briefed | assets_resolved | scored.
Fallidas / bloqueadas / aparcadas / archivadas no cuentan. Ya existentes se
ignoran (no consumen hueco ni petición de detalle).

Salida: una línea JSON con el resumen. Exit 0 = OK (aunque no inserte nada),
2 = no se pudo leer el catálogo (red/DNS/HTTP) → el cron lo ve como error.

    python scripts/whop_discovery.py --max-active 3
    python scripts/whop_discovery.py --dry-run

Env (todas opcionales): DISCOVERY_PLATFORM=youtube, DISCOVERY_MIN_RATE_USD=1.0,
DISCOVERY_MIN_REMAINING_USD=1500, DISCOVERY_MAX_SPENT_PCT=85,
DISCOVERY_ALLOW_APPLICATION=0, DISCOVERY_CONTENT_TYPE=clipping,
DISCOVERY_SHORTLIST=20, DISCOVERY_PAUSE_S=1.5, DISCOVERY_HTTP_RETRIES=3,
DISCOVERY_HTTP_BACKOFF_S=5, DISCOVERY_MAX_PAGES=30, WHOP_TENANT_URL.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path("/opt/clipping-system")
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

ACTIVE_STATUSES = (
    "discovered",
    "briefed",
    "assets_resolved",
    "scored",
)
DEFAULT_MAX_ACTIVE = int(os.environ.get("DISCOVERY_MAX_ACTIVE", "3"))

logger = logging.getLogger("whop_discovery")


def _setup_logging() -> None:
    try:
        log_dir = ROOT / "logs"
        log_dir.mkdir(exist_ok=True)
        handlers: list[logging.Handler] = [logging.FileHandler(log_dir / "whop_discovery.log")]
    except OSError:
        handlers = [logging.StreamHandler(sys.stderr)]
    logging.basicConfig(
        level=os.environ.get("WHOP_DISCOVERY_LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )


def _active_count(db) -> int:
    from app.models.campaign import Campaign

    return db.query(Campaign).filter(Campaign.status.in_(ACTIVE_STATUSES)).count()


def _already_known(db, detail_url: str) -> bool:
    from app.models.campaign import Campaign

    if not detail_url:
        return False
    return db.query(Campaign.id).filter(Campaign.source_url == detail_url).first() is not None


def _detail_url(card: dict) -> str:
    return (
        f"https://whop.com/experiences/{card.get('organizationExperienceId') or ''}"
        f"/campaigns/{card.get('id') or ''}"
    )


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


def run(args, db, client=None) -> tuple[int, dict]:
    from app.services.discovery.whop_catalog import (
        CatalogClient,
        CatalogFetchError,
        DiscoveryFilters,
        select_candidates,
    )

    filters = DiscoveryFilters.from_env()
    if args.shortlist is not None:
        filters.shortlist_size = args.shortlist
    summary: dict = {
        "provider": "whop",
        "catalog": 0,
        "filtered": 0,
        "shortlist": 0,
        "accepted": 0,
        "upserted": 0,
        "skipped_full": 0,
        "active": 0,
        "slots": 0,
        "campaigns": [],
        "dry_run": args.dry_run,
        "max_active": args.max_active,
    }
    active = _active_count(db)
    slots = max(0, args.max_active - active)
    summary["active"] = active
    summary["slots"] = slots
    if slots == 0 and not args.dry_run:
        logger.info("discovery skip: active=%s max=%s", active, args.max_active)
        return 0, summary

    client = client or CatalogClient()
    try:
        accepted, stats = select_candidates(
            client,
            filters,
            is_known=lambda url: _already_known(db, url),
            detail_url=_detail_url,
            want=max(slots, 1) if not args.dry_run else max(slots, 3),
        )
    except CatalogFetchError as e:
        logger.error("whop catalog fetch failed: %s", e)
        summary["error"] = f"catalog_fetch_failed: {e}"
        summary["requests"] = client.requests_made
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
        return 0, summary

    from app.services.discovery.upsert import upsert_campaign

    upserted = 0
    for cand in accepted:
        if upserted >= slots:
            summary["skipped_full"] += 1
            continue
        d = candidate_to_discovered(cand)
        if d is None:
            continue
        c = upsert_campaign(db, d, status="discovered")
        upserted += 1
        summary["campaigns"].append(
            {
                "id": c.id,
                "name": c.name,
                "status": c.status,
                "youtube_rate_usd": cand.economics.get("target_rate_usd"),
                "remaining_usd": cand.economics.get("remaining_usd"),
                "ev": cand.ev,
            }
        )
    summary["upserted"] = upserted
    summary["active"] = _active_count(db)
    summary["slots"] = max(0, args.max_active - summary["active"])
    return 0, summary


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Whop discovery cron entrypoint (#21)")
    p.add_argument("--max-active", type=int, default=DEFAULT_MAX_ACTIVE)
    p.add_argument("--shortlist", type=int, default=None, help="max detail requests (default DISCOVERY_SHORTLIST)")
    p.add_argument("--dry-run", action="store_true")
    # Legacy flags kept so old crontab lines keep working; ignored.
    p.add_argument("--limit", type=int, default=None, help=argparse.SUPPRESS)
    p.add_argument("--fetch-detail", action=argparse.BooleanOptionalAction, default=None, help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    _setup_logging()

    from app.db.database import SessionLocal

    db = SessionLocal()
    try:
        code, summary = run(args, db)
    except Exception as e:  # noqa: BLE001
        logger.exception("whop_discovery failed: %s", e)
        print(json.dumps({"provider": "whop", "error": f"{type(e).__name__}: {e}"}))
        return 1
    finally:
        db.close()
    print(json.dumps(summary, default=str))
    logger.info("whop_discovery done: %s", {k: v for k, v in summary.items() if k != "campaigns"})
    if code:
        print(f"whop_discovery ERROR: {summary.get('error')}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
