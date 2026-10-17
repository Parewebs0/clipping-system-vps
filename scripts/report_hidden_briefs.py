#!/usr/bin/env python3
"""Read-only report: campaigns whose Whop brief is hidden (needs_join).

Never writes. Lists campaigns where detail.brief_counts.references > 0 and no
reference URL is stored. detail.brief_counts is filled by campaign_closed_tick
/ discovery from this release on, so older rows show up after their next
refresh.

    python scripts/report_hidden_briefs.py
    python scripts/report_hidden_briefs.py --status failed_resolve
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path("/opt/clipping-system")
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass


def rows(db, statuses: list[str] | None):
    from app.models.campaign import Campaign
    from app.services.brief_access import hidden_reference_count

    q = db.query(Campaign).filter(Campaign.source_provider == "whop")
    if statuses:
        q = q.filter(Campaign.status.in_(statuses))
    out = []
    for c in q.order_by(Campaign.id.asc()).all():
        n = hidden_reference_count(c.source_metadata)
        if n:
            out.append((c.id, c.status, n, c.name))
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="List hidden-brief (needs_join) campaigns; read-only")
    p.add_argument("--status", action="append", default=None)
    args = p.parse_args(argv)
    from app.db.database import SessionLocal

    db = SessionLocal()
    try:
        found = rows(db, args.status)
    finally:
        db.rollback()
        db.close()
    for cid, status, n, name in found:
        print(f"campaign={cid} status={status} hidden_references={n} name={name[:80]}")
    print(f"hidden_briefs={len(found)} (read-only, nothing changed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
