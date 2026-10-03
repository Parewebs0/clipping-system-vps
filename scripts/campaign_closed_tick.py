#!/usr/bin/env python3
"""Tick: aparca campañas cerradas / agotadas / no aptas en origen (issue #23).

Para cada campaña Whop que no esté archivada ni aparcada consulta el detalle
público (GET {tenant}/api/campaign/campaigns/{id}, cliente educado de #21) y,
si hay motivo (ver app/services/campaign_closed.py), la pasa a `parked` con
el motivo en source_metadata.status_history y source_metadata.park.
Siempre refresca source_metadata.economics / detail / closed_check.

Exit 0 = OK; 2 = fallo de red en todas las consultas (no aparca nada por error).

    python scripts/campaign_closed_tick.py
    python scripts/campaign_closed_tick.py --dry-run --campaign-id 14
    python scripts/campaign_closed_tick.py --no-fit   # solo cierre/agotamiento
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
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
    from app.models.campaign import Campaign, CampaignStatus
    from app.services.campaign_closed import campaign_uuid_from_url, park_reasons, thresholds
    from app.services.campaign_transitions import record_auto_transition
    from app.services.discovery.whop_catalog import CatalogClient, CatalogFetchError

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
    now = datetime.now(timezone.utc).isoformat()
    for c in rows:
        uid = campaign_uuid_from_url(c.source_url)
        if not uid:
            summary["errors"] += 1
            summary["campaigns"].append({"id": c.id, "error": "no_uuid_in_source_url"})
            continue
        try:
            detail = client.fetch_detail(uid)
            reasons, econ, summ = park_reasons(detail, check_fit=not args.no_fit, th=th)
        except CatalogFetchError as e:
            if "HTTP 404" in str(e):
                reasons, econ, summ = ["not_found_on_source"], {}, {}
            else:
                summary["errors"] += 1
                summary["campaigns"].append({"id": c.id, "error": str(e)[:200]})
                continue
        entry = {"id": c.id, "status": c.status, "reasons": reasons,
                 "spent_pct": econ.get("spent_pct"), "remaining_usd": econ.get("remaining_usd"),
                 "youtube_rate_usd": econ.get("target_rate_usd")}
        summary["campaigns"].append(entry)
        if args.dry_run:
            summary["parked" if reasons else "kept"] += 1
            continue
        meta = dict(c.source_metadata or {})
        if econ:
            meta["economics"] = {**(meta.get("economics") or {}), **econ}
        if summ:
            meta["detail"] = summ
        meta["closed_check"] = {"at": now, "reasons": reasons}
        if reasons:
            meta["park"] = {"at": now, "reasons": reasons, "previous_status": c.status}
            c.source_metadata = meta
            record_auto_transition(c, CampaignStatus.PARKED.value, reason="; ".join(reasons),
                                   actor="campaign_closed_tick")
            summary["parked"] += 1
        else:
            c.source_metadata = meta
            summary["kept"] += 1
        db.commit()
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
        print(f"campaign={e['id']} " + (f"error={e['error']}" if "error" in e else
              ("-> parked " + "; ".join(e["reasons"]) if e["reasons"] else "ok")
              + f" spent={e.get('spent_pct')}% remaining={e.get('remaining_usd')} yt={e.get('youtube_rate_usd')}"))
    print(json.dumps({k: v for k, v in summary.items() if k != "campaigns"}, default=str))
    return code


if __name__ == "__main__":
    sys.exit(main())
