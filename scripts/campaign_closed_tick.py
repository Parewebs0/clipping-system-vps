#!/usr/bin/env python3
"""Tick: refresca campañas Whop y las aparca o archiva según el origen (#23, #72).

Para cada campaña Whop que no esté archivada ni aparcada consulta el detalle
público y aplica app/services/campaign_closed.py: presupuesto, CPM, fechas,
last_synced_at e histórico. Cerrada, desaparecida o sin presupuesto sale del
pipeline; por debajo del umbral, o no apta, queda aparcada. No cancela jobs.

Exit 0 = OK; 2 = fallo de red en todas las consultas.

    python scripts/campaign_closed_tick.py
    python scripts/campaign_closed_tick.py --dry-run --campaign-id 14
    python scripts/campaign_closed_tick.py --no-fit   # solo cierre/agotamiento
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path("/opt/clipping-system")
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass


def _checkable_statuses() -> tuple[str, ...]:
    from app.models.campaign import CampaignStatus as S

    skip = {S.ARCHIVED.value, S.PARKED.value}
    return tuple(s.value for s in S if s.value not in skip)


def run(args, db, client=None) -> tuple[int, dict]:
    from app.models.campaign import Campaign
    from app.services.campaign_closed import sync_whop_campaign, thresholds
    from app.services.discovery.whop_catalog import CatalogClient

    th = thresholds()
    q = db.query(Campaign).filter(Campaign.source_provider == "whop")
    if args.campaign_id:
        q = q.filter(Campaign.id == args.campaign_id)
    else:
        q = q.filter(Campaign.status.in_(_checkable_statuses()))
    rows = q.order_by(Campaign.id.asc()).limit(args.limit).all()
    client = client or CatalogClient()
    summary = {"scanned": len(rows), "parked": 0, "kept": 0, "errors": 0, "dry_run": args.dry_run,
               "thresholds": th, "campaigns": []}
    for c in rows:
        entry = sync_whop_campaign(
            db, c, client,
            dry_run=args.dry_run,
            check_fit=not args.no_fit,
            actor="campaign_closed_tick",
            th=th,
        )
        summary["campaigns"].append(entry)
        outcome = entry.get("outcome")
        if outcome == "error":
            summary["errors"] += 1
        elif outcome in ("parked", "kept"):
            summary[outcome] += 1
        elif outcome and outcome != "skipped":
            summary[outcome] = summary.get(outcome, 0) + 1
    summary["requests"] = client.requests_made
    code = 2 if rows and summary["errors"] == len(rows) else 0
    return code, summary


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Park closed/exhausted/unfit campaigns (#23)")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--campaign-id", type=int, default=None)
    p.add_argument("--no-fit", action="store_true", help="only closure/exhaustion, skip fit checks")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    from app.db.database import SessionLocal

    db = SessionLocal()
    try:
        code, summary = run(args, db)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        print(f"campaign_closed_tick ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        db.close()
    for e in summary["campaigns"]:
        if e.get("outcome") == "error":
            print(f"campaign={e['id']} error={e.get('error')}")
            continue
        reasons = "; ".join(e.get("reasons") or [])
        extra = f" {reasons}" if reasons else ""
        print(
            f"campaign={e['id']} {e.get('outcome')}{extra}"
            f" spent={e.get('spent_pct')}% remaining={e.get('remaining_usd')} yt={e.get('youtube_rate_usd')}"
        )
    print(json.dumps({k: v for k, v in summary.items() if k != "campaigns"}, default=str))
    return code


if __name__ == "__main__":
    sys.exit(main())
