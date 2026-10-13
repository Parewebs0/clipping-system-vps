#!/usr/bin/env python3
"""Paso 0 — Whop / Content Rewards discovery (issues #21, #72).

Always refreshes campaigns that are still in play and queues new ones.
DISCOVERY_MAX_ACTIVE only decides how many leave the queue and enter the
pipeline (brief, then download/render), highest score first. needs_review
does not take a slot.

Exit 0 = OK (even when nothing is inserted), 2 = the catalog could not be
read (network/DNS/HTTP). Stdout is a JSON summary plus one `discovery …` line
with how many were refreshed, entered, filed away, held, and activated.

    python scripts/whop_discovery.py --max-active 3
    python scripts/whop_discovery.py --dry-run

Env (all optional): DISCOVERY_PLATFORM=youtube, DISCOVERY_MIN_RATE_USD=1.0,
DISCOVERY_MIN_REMAINING_USD=1500, DISCOVERY_MAX_SPENT_PCT=85,
DISCOVERY_ALLOW_APPLICATION=0, DISCOVERY_CONTENT_TYPE=clipping,
DISCOVERY_SHORTLIST=20, DISCOVERY_PAUSE_S=1.5, DISCOVERY_HTTP_RETRIES=3,
DISCOVERY_HTTP_BACKOFF_S=5, DISCOVERY_MAX_PAGES=30, WHOP_TENANT_URL,
MIN_REMAINING_BUDGET_USD=500, MIN_REMAINING_BUDGET_PCT=10.
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

# Pipeline occupancy. needs_review waits on a person and must not take a slot.
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


def run(args, db, client=None) -> tuple[int, dict]:
    from app.services.discovery.sync_run import execute

    return execute(args, db, client, active_statuses=ACTIVE_STATUSES)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Whop discovery cron entrypoint (#21, #72)")
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
    if summary.get("log"):
        print(summary["log"])
        logger.info("%s", summary["log"])
    if code:
        print(f"whop_discovery ERROR: {summary.get('error')}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
