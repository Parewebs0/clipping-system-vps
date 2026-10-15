#!/usr/bin/env python3
"""YouTube Analytics tick. Reads views and retention for posted clips.

Dry-run by default: prints the clips it would refresh and does not call
YouTube or write the database. ``--live`` needs
YOUTUBE_ANALYTICS_CLIENT_ID / SECRET / REFRESH_TOKEN (falls back to
YOUTUBE_CLIENT_ID / SECRET / REFRESH_TOKEN). The refresh token must include
the yt-analytics.readonly scope. No deploy: run it by hand.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/opt/clipping-system")
if not (ROOT / "app").is_dir():
    ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _env(*names: str) -> str:
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument(
        "--live",
        action="store_true",
        help="call YouTube Analytics and store clips.analytics",
    )
    args = parser.parse_args()

    from app.db.database import SessionLocal
    from app.models.clip_publication import ClipPublication
    from app.services.clip_analytics import sync_youtube_analytics, youtube_video_id

    db = SessionLocal()
    try:
        if not args.live:
            pubs = (
                db.query(ClipPublication)
                .filter(
                    ClipPublication.platform == "youtube",
                    ClipPublication.status == "posted",
                    ClipPublication.post_url.isnot(None),
                )
                .limit(max(1, int(args.limit)))
                .all()
            )
            print(f"clip_analytics_tick dry_run due={len(pubs)}")
            for pub in pubs:
                print(f"clip={pub.clip_id} video={youtube_video_id(pub.post_url)}")
            return 0

        client_id = _env("YOUTUBE_ANALYTICS_CLIENT_ID", "YOUTUBE_CLIENT_ID")
        client_secret = _env("YOUTUBE_ANALYTICS_CLIENT_SECRET", "YOUTUBE_CLIENT_SECRET")
        refresh_token = _env("YOUTUBE_ANALYTICS_REFRESH_TOKEN", "YOUTUBE_REFRESH_TOKEN")
        if not (client_id and client_secret and refresh_token):
            print("clip_analytics_tick ERROR: missing YouTube OAuth env", file=sys.stderr)
            return 1

        import requests

        from app.services.clip_analytics import fetch_youtube_analytics, refresh_access_token

        token = refresh_access_token(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            post=lambda url, data: requests.post(url, data=data, timeout=30).json(),
        )
        today = datetime.now(timezone.utc).date().isoformat()

        def _get(url: str, params: dict, headers: dict) -> dict:
            response = requests.get(url, params=params, headers=headers, timeout=30)
            response.raise_for_status()
            return response.json()

        def _fetch(video_id: str):
            return fetch_youtube_analytics(
                video_id,
                access_token=token,
                start_date="2006-01-01",
                end_date=today,
                get=_get,
            )

        rows = sync_youtube_analytics(db, fetch=_fetch, limit=args.limit)
        print(f"clip_analytics_tick saved={len(rows)}")
        for row in rows:
            print(" ".join(f"{key}={value}" for key, value in row.items()))
        return 0
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        print(f"clip_analytics_tick ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
