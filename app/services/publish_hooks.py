"""Hooks after a publish job completes."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.logctx import format_ids, ids_from_job, redact
from app.models.clip_publication import ClipPublication
from app.models.job import Job
from app.services.clip_storage_service import mark_clip_uploaded

logger = logging.getLogger(__name__)


def on_publish_completed(db: Session, job: Job, result_data: dict) -> None:
    payload = job.payload if isinstance(job.payload, dict) else {}
    result_data = result_data or {}
    fields = format_ids(**ids_from_job(job))
    pub_id = payload.get("publication_id")
    clip_id = payload.get("clip_id")
    dry = bool(result_data.get("dry_run") or payload.get("dry_run"))
    final_path = result_data.get("final_path_worker")
    source_moved = bool(result_data.get("source_moved"))

    pub = None
    if pub_id:
        try:
            pub = db.get(ClipPublication, uuid.UUID(str(pub_id)))
        except (TypeError, ValueError):
            pub = None

    pubs = result_data.get("publications") or []
    first = pubs[0] if pubs else {}

    if source_moved and clip_id:
        try:
            mark_clip_uploaded(
                db,
                uuid.UUID(str(clip_id)),
                final_path_worker=final_path,
            )
        except (TypeError, ValueError) as e:
            logger.warning("publish mark_uploaded skipped %s: %s", fields, redact(str(e)))

    if pub is None:
        logger.info("publish done %s dry_run=%s (no publication row)", fields, dry)
        return

    if dry:
        pub.error_message = "dry_run"
        if first.get("post_url"):
            pub.post_url = first["post_url"]
        db.commit()
        logger.info("publish dry-run %s publication=%s", fields, pub.id)
        return

    status = (first.get("status") or "").lower()
    if status == "posted":
        pub.status = "posted"
        pub.post_url = first.get("post_url")
        pub.posted_at = datetime.now(timezone.utc)
        pub.error_message = None
    elif status == "failed":
        pub.status = "failed"
        pub.error_message = redact(str(first.get("error") or job.error_message or "publish failed"))[:500]
    else:
        # A completed live job with any other status used to leave the
        # publication pending, and the enqueue tick then refused to retry it.
        pub.status = "failed"
        pub.error_message = f"unexpected publish status: {status or 'missing'}"
        logger.warning("publish result not posted %s status=%s", fields, status or "missing")
    db.commit()
    logger.info("publish recorded %s publication=%s status=%s", fields, pub.id, pub.status)
