#!/usr/bin/env python3
"""Summarize llm_usage per day (UTC) and stage.

    docker exec clipping-system-vps-api-1 python scripts/llm_usage_summary.py [--days 7] [--by-campaign]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path("/opt/clipping-system")
sys.path.insert(0, str(ROOT))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--by-campaign", action="store_true", help="group by campaign instead of day")
    args = p.parse_args()

    from sqlalchemy import text
    from app.db.database import SessionLocal
    from app.services.llm_usage import prices

    key = "campaign_id::text" if args.by_campaign else "to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD')"
    sql = text(f"""
        SELECT {key} AS k, stage, count(*) AS calls, sum(CASE WHEN ok THEN 0 ELSE 1 END) AS errors,
               sum(prompt_tokens) AS p, sum(cached_tokens) AS c, sum(completion_tokens) AS o,
               sum(reasoning_tokens) AS r, sum(total_tokens) AS t,
               round(avg(latency_ms)) AS lat, sum(cost_usd) AS cost
        FROM llm_usage
        WHERE created_at >= now() - make_interval(days => :days)
        GROUP BY 1, 2 ORDER BY 1, 2
    """)
    db = SessionLocal()
    try:
        rows = db.execute(sql, {"days": args.days}).all()
    finally:
        db.close()
    label = "campaign" if args.by_campaign else "day(UTC)"
    pr = prices()
    print(f"llm_usage last {args.days}d  prices/1M: in=${pr['input']} cached=${pr['cached']} out=${pr['output']}")
    hdr = f"{label:<12} {'stage':<24} {'calls':>5} {'err':>4} {'prompt':>9} {'cached':>8} {'compl':>8} {'reason':>8} {'total':>9} {'avg_ms':>7} {'cost_usd':>10}"
    print(hdr)
    print("-" * len(hdr))
    tot_calls = tot_tokens = 0
    tot_cost = 0.0
    for k, stage, calls, err, pt, ct, ot, rt, tt, lat, cost in rows:
        print(f"{str(k):<12} {stage:<24} {calls:>5} {err:>4} {pt or 0:>9} {ct or 0:>8} {ot or 0:>8} "
              f"{rt or 0:>8} {tt or 0:>9} {int(lat or 0):>7} {float(cost or 0):>10.4f}")
        tot_calls += calls
        tot_tokens += tt or 0
        tot_cost += float(cost or 0)
    print("-" * len(hdr))
    print(f"{'TOTAL':<12} {'':<24} {tot_calls:>5} {'':>4} {'':>9} {'':>8} {'':>8} {'':>8} {tot_tokens:>9} {'':>7} {tot_cost:>10.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
