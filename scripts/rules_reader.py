#!/usr/bin/env python3
"""Re-read campaign rules with RuleSet v2 (#33). Does NOT change status.

    python scripts/rules_reader.py --campaign-id 18
    python scripts/rules_reader.py --all [--dry-run]
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


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--campaign-id", type=int)
    g.add_argument("--all", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--show", action="store_true", help="print the full RuleSet JSON")
    args = p.parse_args(argv)

    from app.db.database import SessionLocal
    from app.models.campaign import Campaign
    from app.services.grok_client import grok_chat_json
    from app.services.rules.service import ensure_detail, persist_rules, read_rules

    db = SessionLocal()
    rc = 0
    try:
        q = db.query(Campaign).order_by(Campaign.id.asc())
        rows = q.filter(Campaign.id == args.campaign_id).all() if args.campaign_id else q.all()
        for c in rows:
            try:
                ensure_detail(c)
                rs, bundle = read_rules(c, grok_chat_json)
            except Exception as e:  # noqa: BLE001
                print(f"campaign={c.id} ERROR {type(e).__name__}: {e}")
                rc = 1
                continue
            cov = rs.coverage
            print(json.dumps({
                "campaign": c.id, "status": c.status,
                "docs": [(d["ok"], d["chars"]) for d in bundle.docs],
                "source_chars": rs.meta.get("source_chars"),
                "coverage": {"lines": cov.normative_lines, "first": cov.covered_first_pass,
                             "second_mapped": cov.mapped_second_pass, "unsupported": cov.unsupported_second_pass,
                             "ignored": cov.ignored, "uncovered": len(cov.uncovered),
                             "evidence_verified": f"{cov.evidence_verified}/{cov.evidence_total}"},
                "unsupported": [u.text for u in rs.unsupported],
                "account": [a.kind for a in rs.account_requirements],
                "manual_checks": len(rs.manual_checks),
                "duration": [rs.duration.min_s, rs.duration.max_s],
                "validation_errors": rs.meta.get("validation_errors"),
            }, ensure_ascii=False))
            if args.show:
                print(json.dumps(rs.dump(), ensure_ascii=False, indent=1))
            if args.dry_run:
                db.rollback()
                continue
            persist_rules(c, rs, bundle)
            db.commit()
        return rc
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
