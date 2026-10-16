#!/usr/bin/env python3
"""One-off: apply the decider score thresholds to existing pending candidates.

score >= --approve-min is approved (queues a GPU render), score < --reject-below
is rejected, the middle band stays pending. needs_review is not touched.
Dry-run by default; pass --apply to write."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path("/opt/clipping-system")
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception as exc:
    print(f"dotenv not loaded: {type(exc).__name__}", file=sys.stderr)


def main() -> int:
    from app.services.candidate_thresholds import (
        apply_thresholds,
        default_approve_min,
        default_reject_below,
        validate_thresholds,
    )

    p = argparse.ArgumentParser()
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="default: only print what would change")
    mode.add_argument("--apply", action="store_true", help="really approve/reject")
    p.add_argument("--approve-min", type=float, default=None)
    p.add_argument("--reject-below", type=float, default=None)
    p.add_argument("--campaign-id", type=int, default=None)
    args = p.parse_args()
    approve_min = args.approve_min if args.approve_min is not None else default_approve_min()
    reject_below = args.reject_below if args.reject_below is not None else default_reject_below()
    validate_thresholds(approve_min, reject_below)
    dry_run = not args.apply

    from app.db.database import SessionLocal
    from app.models.candidate import Candidate
    from app.models.job import Job  # noqa: F401

    db = SessionLocal()
    try:
        q = db.query(Candidate).filter(Candidate.status == "pending")
        if args.campaign_id is not None:
            q = q.filter(Candidate.campaign_id == args.campaign_id)
        rows = q.order_by(Candidate.created_at.asc()).all()
        counts = apply_thresholds(
            db, rows, approve_min=approve_min, reject_below=reject_below, dry_run=dry_run
        )
        print(
            f"apply_candidate_thresholds {'dry-run' if dry_run else 'applied'} "
            f"approve_min={approve_min} reject_below={reject_below} "
            f"approve={counts['approve']} reject={counts['reject']} keep={counts['keep']}"
        )
        return 0
    except Exception as e:
        db.rollback()
        from app.logctx import redact
        print(f"ERROR {type(e).__name__}: {redact(str(e))}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
