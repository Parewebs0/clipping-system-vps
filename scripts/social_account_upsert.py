#!/usr/bin/env python3
"""Upsert one social_accounts row via SessionLocal. No tokens, no network.

    python scripts/social_account_upsert.py --platform tiktok --account-key default --handle @x --status healthy

Ops only. Does not print secrets (there are none on this row).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Upsert a social account (no tokens).")
    p.add_argument("--platform", required=True)
    p.add_argument("--account-key", default="default")
    p.add_argument("--handle", default=None)
    p.add_argument("--status", default=None)
    p.add_argument("--auth-kind", default=None)
    args = p.parse_args(argv)

    from pydantic import ValidationError

    from app.schemas.clip import SocialAccountCreate
    from app.services.social_accounts import format_validation_error

    probe: dict = {"platform": args.platform, "account_key": args.account_key}
    if args.handle is not None:
        probe["handle"] = args.handle
    if args.status is not None:
        probe["status"] = args.status
    if args.auth_kind is not None:
        probe["auth_kind"] = args.auth_kind
    try:
        SocialAccountCreate.model_validate(probe)
    except ValidationError as exc:
        print(f"social_account_upsert ERROR: {format_validation_error(exc)}", file=sys.stderr)
        return 2

    from app.db.database import SessionLocal
    from app.services.social_accounts import SocialAccountConflict, SocialAccountInvalid, upsert_social_account

    db = SessionLocal()
    try:
        row, action = upsert_social_account(
            db,
            platform=args.platform,
            account_key=args.account_key,
            handle=args.handle,
            status=args.status,
            auth_kind=args.auth_kind,
            handle_set=args.handle is not None,
        )
        handle = (row.handle or "").replace("\n", " ").replace("\r", "")
        summary = (
            "social_account_upsert "
            f"action={action} platform={row.platform} account_key={row.account_key} "
            f"status={row.status} auth_kind={row.auth_kind} handle={handle}"
        )
    except (SocialAccountInvalid, SocialAccountConflict) as exc:
        print(f"social_account_upsert ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 — never print the URL or a secret
        print(f"social_account_upsert ERROR: {type(exc).__name__}", file=sys.stderr)
        return 1
    finally:
        db.close()

    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
