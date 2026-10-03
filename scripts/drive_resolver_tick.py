#!/usr/bin/env python3
"""Paso 3b — un solo resolver, varios backends (Drive, Dropbox file, URL directa).

Cron path kept as drive_resolver_tick.py.
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

_RETRYABLE = {"gog", "gog_error"}


def _candidate_urls(campaign) -> list[str]:
    meta = campaign.source_metadata or {}
    urls: list[str] = []
    for u in meta.get("asset_links") or []:
        if isinstance(u, str):
            urls.append(u)
    rules = meta.get("rules") or {}
    for u in rules.get("content_source_urls") or []:
        if isinstance(u, str):
            urls.append(u)
    discovered = meta.get("discovered") or {}
    detail = meta.get("detail") or {}
    refs = list(discovered.get("reference_materials") or []) + list(detail.get("reference_material") or [])
    for ref in refs:
        if isinstance(ref, dict) and ref.get("url"):
            urls.append(ref["url"])
        elif isinstance(ref, str):
            urls.append(ref)
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=1)
    p.add_argument("--campaign-id", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    from app.db.database import SessionLocal
    from app.models.campaign import Campaign
    from app.models.asset import Asset
    from app.schemas.asset import AssetCreate
    from app.services.asset_service import create_asset
    from app.services.asset_resolve import RESOLVER_VERSION, expand_all
    from app.services.campaign_transitions import lock_if_status

    step_statuses = ("briefed", "failed_resolve", "assets_resolved")

    db = SessionLocal()
    created = resolved = 0
    try:
        q = db.query(Campaign)
        if args.campaign_id:
            camps = q.filter(Campaign.id == args.campaign_id).all()
        else:
            # No SQL LIMIT here: terminal failed_resolve / already-resolved
            # campaigns are skipped below and must not eat --limit slots,
            # otherwise they starve newer briefed campaigns (ordered by id).
            camps = (
                q.filter(Campaign.status.in_(("briefed", "failed_resolve", "assets_resolved")))
                .order_by(Campaign.id.asc())
                .all()
            )
        print(f"asset_resolver_tick scanned={len(camps)} dry_run={args.dry_run}")
        processed = skipped = 0
        for c in camps:
            if processed >= args.limit:
                break
            meta = dict(c.source_metadata or {})
            prev_err = meta.get("resolve_error") or {}
            prev = prev_err.get("kind")
            stale = int(prev_err.get("resolver_version") or 1) < RESOLVER_VERSION
            if c.status == "failed_resolve" and prev not in _RETRYABLE and not stale and not args.campaign_id:
                skipped += 1
                continue
            existing = db.query(Asset).filter(Asset.campaign_id == c.id).all()
            if c.status == "assets_resolved" and any(
                (a.extra_metadata or {}).get("kind") not in {"drive_folder", "brand_asset", "brief_doc"}
                for a in existing
            ):
                skipped += 1
                continue
            processed += 1
            urls = _candidate_urls(c)
            print(f"campaign={c.id} urls={len(urls)}")
            items, errors = expand_all(urls)
            existing_ids = {a.source_id for a in existing if a.source_id}
            new_assets = 0
            for item in items:
                sid = item["source_id"]
                if sid in existing_ids:
                    continue
                print(
                    f"campaign={c.id} + {item['source_provider']} "
                    f"id={sid} name={item['name']}"
                )
                if args.dry_run:
                    new_assets += 1
                    continue
                create_asset(
                    db,
                    AssetCreate(
                        campaign_id=c.id,
                        source_url=item["source_url"],
                        source_id=sid,
                        source_provider=item["source_provider"],
                        asset_type="video",
                        extra_metadata={
                            "kind": item["kind"],
                            "name": item["name"],
                            "size": item.get("size"),
                            "discovered_by": "asset_resolver_tick",
                            "resolver_version": RESOLVER_VERSION,
                        },
                    ),
                )
                existing_ids.add(sid)
                new_assets += 1
                created += 1
            if new_assets == 0:
                gog_fail = any(str(e).startswith("gog:") for e in errors)
                if gog_fail:
                    kind = "gog"
                elif errors == ["social_only"]:
                    # Only social/reference links (IG audio, TikTok, YT sound pages,
                    # profiles): nothing to download. Terminal, never retried.
                    kind = "social_only"
                elif errors:
                    kind = "unsupported_source"
                else:
                    kind = "no_videos"
                if kind == "social_only":
                    errors = ["social_only: only social/reference links, no Drive/Dropbox/direct video source"]
                print(f"campaign={c.id} new_files=0 errors={errors[:3]} -> failed_resolve/{kind}")
                if not args.dry_run:
                    if not lock_if_status(db, c, step_statuses):
                        print(f"campaign={c.id} moved to {c.status} meanwhile -> left untouched")
                        db.commit()
                        continue
                    meta = dict(c.source_metadata or {})
                    c.status = "failed_resolve"
                    meta["resolve_error"] = {
                        "kind": kind,
                        "message": "; ".join(errors)[:300] if errors else "no ingestible videos",
                        "resolver_version": RESOLVER_VERSION,
                    }
                    c.source_metadata = meta
                    db.commit()
                continue
            if not args.dry_run:
                if not lock_if_status(db, c, step_statuses):
                    print(f"campaign={c.id} moved to {c.status} meanwhile -> assets kept, status untouched")
                    db.commit()
                    continue
                meta = dict(c.source_metadata or {})
                c.status = "assets_resolved"
                meta["resolve_error"] = None
                c.source_metadata = meta
                db.commit()
                resolved += 1
            print(f"campaign={c.id} new_files={new_assets} -> assets_resolved")
        print(f"asset_resolver_tick processed={processed} skipped_terminal={skipped} created={created} resolved={resolved}")
        return 0
    except Exception as e:
        db.rollback()
        print(f"asset_resolver_tick ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
