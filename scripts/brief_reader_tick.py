#!/usr/bin/env python3
"""Paso 3a — brief-reader. RuleSet v2 (#33): structured mapping + LLM per chunk + coverage pass.

Reads source_metadata.discovered. Fetches public Google Docs.
Success always status=briefed (rules + score_preview written).
Blocking/scoring is 3c; 3b no-ops if there is no Drive folder.

    python scripts/brief_reader_tick.py --limit 1
    python scripts/brief_reader_tick.py --campaign-id 8
"""
from __future__ import annotations

import argparse
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

def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=1)
    p.add_argument("--campaign-id", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    from app.db.database import SessionLocal
    from app.models.campaign import Campaign
    from app.services.grok_client import grok_chat_json
    from app.services.campaign_score import campaign_rate_usd, campaign_remaining_usd, score_campaign
    from app.services.rules.service import ensure_detail, persist_rules, read_rules

    db = SessionLocal()
    try:
        q = db.query(Campaign)
        if args.campaign_id:
            rows = q.filter(Campaign.id == args.campaign_id).all()
        else:
            rows = (
                q.filter(Campaign.status == "discovered")
                .order_by(Campaign.id.asc())
                .limit(args.limit)
                .all()
            )
        print(f"brief_reader_tick scanned={len(rows)} dry_run={args.dry_run}")
        for c in rows:
            meta = dict(c.source_metadata or {})
            if not meta.get("discovered") and not meta.get("detail"):
                print(f"campaign={c.id} failed_brief no_materials")
                if not args.dry_run:
                    c.status = "failed_brief"
                    meta["briefing_error"] = {
                        "kind": "no_materials",
                        "message": "no discovered payload",
                        "at": datetime.now(timezone.utc).isoformat(),
                    }
                    c.source_metadata = meta
                    db.commit()
                continue
            if args.dry_run:
                print(f"campaign={c.id} would read rules (RuleSet v2)")
                continue
            try:
                ensure_detail(c)
                rs, bundle = read_rules(c, grok_chat_json)
            except Exception as e:
                print(f"campaign={c.id} failed_brief {e}")
                db.rollback()
                c = db.get(Campaign, c.id)
                meta = dict(c.source_metadata or {})
                c.status = "failed_brief"
                meta["briefing_error"] = {
                    "kind": "llm_timeout" if "timeout" in str(e).lower() else "other",
                    "message": str(e)[:300],
                    "at": datetime.now(timezone.utc).isoformat(),
                }
                c.source_metadata = meta
                db.commit()
                continue
            rules = persist_rules(c, rs, bundle)
            meta = dict(c.source_metadata or {})
            preview = score_campaign(
                real_assets=0 if rules.get("unsupported_video_host") else 1,
                cpm=campaign_rate_usd(meta),
                prize=campaign_remaining_usd(meta),
                verified=bool((meta.get("discovered") or {}).get("organization_verified")),
                rules=rules,
                content_kinds=rules.get("content_source_kinds"),
            )
            meta["score_preview"] = preview
            meta["briefing_error"] = None
            c.source_metadata = meta
            c.status = "briefed"
            db.commit()
            cov = rs.coverage
            print(
                f"campaign={c.id} -> briefed ruleset_v{rs.version} "
                f"duration={rs.duration.min_s}-{rs.duration.max_s} docs={len(bundle.docs)} "
                f"coverage={cov.covered_first_pass}+{cov.mapped_second_pass}/{cov.normative_lines} "
                f"unsupported={len(rs.unsupported)} account={len(rs.account_requirements)} "
                f"blockers={len(meta.get('rules_blockers') or [])} score_preview={preview['value']}"
            )
        return 0
    except Exception as e:
        db.rollback()
        print(f"brief_reader_tick ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
