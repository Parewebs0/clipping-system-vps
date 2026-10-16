"""State transition extensions for job_service."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.logctx import format_ids, ids_from_job, redact
from app.models.asset import Asset, AssetStatus
from app.models.campaign import Campaign, CampaignStatus
from app.services.download_errors import PERMANENT_DOWNLOAD_KINDS, permanent_download_kind
from app.models.clip import Clip, ClipQAStatus, ClipStatus
from app.models.job import Job, JobStatus  # JobType is a string, not enum
from app.services.job_service import create_job

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def reframe_wants_diarization(campaign) -> bool:
    """Pyannote only runs when this campaign's reframe mode is not the center crop."""
    meta = getattr(campaign, "source_metadata", None) or {}
    if not isinstance(meta, dict):
        return False
    overrides = meta.get("rules_overrides")
    if not isinstance(overrides, dict):
        return False
    reframe = overrides.get("reframe")
    if not isinstance(reframe, dict):
        return False
    mode = str(reframe.get("mode") or "center").strip().lower()
    return mode != "center"


def build_transcribe_payload(asset, meta: dict | None = None, campaign=None) -> dict:
    """Worker transcribe payload.

    Diarization is on only when the campaign opted into a reframe mode other
    than center. The worker already treats a diarization failure as non-fatal.
    """
    meta = meta or {}
    video = asset.local_path or asset.source_url
    return {
        "asset_id": str(asset.id),
        "campaign_id": str(asset.campaign_id),
        "video": video,
        "video_path": video,
        "source_url": asset.source_url,
        "language": meta.get("language"),
        "diarization": reframe_wants_diarization(campaign),
    }


def _update_asset_status(db: Session, asset: Asset, new_status: str) -> None:
    if new_status not in (
        AssetStatus.PENDING.value,
        AssetStatus.DOWNLOADED.value,
        AssetStatus.TRANSCRIBED.value,
        AssetStatus.FAILED.value,
    ):
        logger.warning("invalid asset status: %s", new_status)
        return
    asset.status = new_status
    now = _now()
    if new_status == AssetStatus.DOWNLOADED.value and asset.downloaded_at is None:
        asset.downloaded_at = now
    if new_status == AssetStatus.TRANSCRIBED.value and asset.transcribed_at is None:
        asset.transcribed_at = now
    db.commit()
    db.refresh(asset)
    logger.info("asset %s -> %s", asset.id, new_status)


def _asset_id_from_payload(payload: dict) -> Optional[str]:
    val = payload.get("asset_id") if isinstance(payload, dict) else None
    if not val:
        val = payload.get("source_asset_id") if isinstance(payload, dict) else None
    return val


def _brand_initial_prompt(db: Session, asset: Asset) -> Optional[str]:
    """brand_dictionary as WhisperX initial_prompt (MOLI-54).

    Vocabulary-only: names/spellings joined by spaces, capped (WhisperX keeps
    ~224 tokens of prompt context). Never contains instructions — it just
    biases the spelling of brand terms. Returns None when there is nothing.
    """
    try:
        campaign = db.get(Campaign, asset.campaign_id)
        if campaign is None:
            return None
        from app.services.rules.schema import load_ruleset

        rs = load_ruleset(campaign.source_metadata or {})
        if rs is None:
            return None
        words = [str(w).strip() for w in (rs.captions.brand_dictionary or []) if str(w).strip()]
        if not words:
            return None
        prompt = " ".join(words)
        return prompt[:900] or None
    except Exception:  # noqa: BLE001 - a broken branding config never blocks transcription
        logger.warning("brand initial_prompt failed for asset %s", asset.id, exc_info=True)
        return None


def on_download_completed(
    db: Session, job: Job, result_data: dict
) -> None:
    asset_id = _asset_id_from_payload(job.payload or {})
    if not asset_id:
        logger.warning("download job %s completed but no asset_id in payload", job.id)
        return
    asset = db.get(Asset, uuid.UUID(asset_id))
    if asset is None:
        logger.warning("download job %s: asset %s not found", job.id, asset_id)
        return

    _update_asset_status(db, asset, AssetStatus.DOWNLOADED.value)
    if job.worker_id:
        asset.extra_metadata = {**(asset.extra_metadata or {}), "downloaded_by": job.worker_id}
    if "file_path" in result_data:
        asset.local_path = result_data["file_path"]
    if "file_size" in result_data:
        asset.file_size = int(result_data["file_size"])
    if "duration_seconds" in result_data:
        asset.duration_seconds = float(result_data["duration_seconds"])
    if "sha256" in result_data:
        asset.sha256 = result_data["sha256"]
    if "mime_type" in result_data:
        asset.mime_type = result_data["mime_type"]
    db.commit()
    db.refresh(asset)

    from app.services.download_payload_validation import (
        validate_download_result, MediaFormatInvalid,
    )
    meta = dict(asset.extra_metadata or {})
    try:
        validate_download_result(
            file_path=asset.local_path,
            mime_type=asset.mime_type or meta.get("mime_type"),
            result_data=result_data,
            kind=meta.get("kind"),
            source_name=meta.get("name"),
        )
    except MediaFormatInvalid as e:
        meta["skip_download"] = True
        meta["last_error_kind"] = "unsupported_media_format"
        meta["last_error_detail"] = str(e)[:500]
        meta["terminal_failed_at"] = _now().isoformat()
        meta["file_path_reported"] = (asset.local_path or "")[:500]
        asset.extra_metadata = meta
        _update_asset_status(db, asset, AssetStatus.FAILED.value)
        logger.warning(
            "asset %s NOT transcribed: invalid media format (%s) file_path=%s mime=%s",
            asset.id, e, asset.local_path, asset.mime_type,
        )
        return

    try:
        create_job(
            db,
            job_type="transcribe",
            payload={
                **build_transcribe_payload(asset, meta, db.get(Campaign, asset.campaign_id)),
                # MOLI-54: brand vocabulary biases WhisperX spellings.
                "initial_prompt": _brand_initial_prompt(db, asset),
            },
            priority=job.priority,
            max_attempts=job.max_attempts,
            affinity_worker_id=job.worker_id,
        )
        logger.info(
            "auto-created transcribe %s",
            format_ids(
                job_id=job.id,
                job_type="transcribe",
                campaign_id=asset.campaign_id,
                asset_id=asset.id,
            ),
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("failed to auto-create transcribe job: %s", e)


def on_transcribe_completed(
    db: Session, job: Job, result_data: dict
) -> None:
    asset_id = _asset_id_from_payload(job.payload or {})
    if not asset_id:
        logger.warning("transcribe job %s completed but no asset_id in payload", job.id)
        return
    asset = db.get(Asset, uuid.UUID(asset_id))
    if asset is None:
        logger.warning("transcribe job %s: asset %s not found", job.id, asset_id)
        return

    _update_asset_status(db, asset, AssetStatus.TRANSCRIBED.value)
    meta = dict(asset.extra_metadata or {})
    meta["transcription"] = result_data
    asset.extra_metadata = meta
    db.commit()
    db.refresh(asset)
    logger.info(
        "transcription stored %s",
        format_ids(
            job_id=job.id,
            job_type="transcribe",
            campaign_id=asset.campaign_id,
            asset_id=asset.id,
        ),
    )

    try:
        from app.services.silent_clip_cutter import maybe_cut_silent
        cut = maybe_cut_silent(db, asset)
        logger.info("post-transcribe clip route asset=%s %s", asset.id, cut)
    except Exception as e:  # noqa: BLE001
        logger.exception("silent cut failed asset %s: %s", asset.id, e)


def on_render_completed(
    db: Session, job: Job, result_data: dict
) -> tuple[Optional[Clip], Optional[Job]]:
    asset_id = _asset_id_from_payload(job.payload or {})
    if not asset_id:
        return None, None
    asset = db.get(Asset, uuid.UUID(asset_id))
    if asset is None:
        return None, None

    candidate_id = None
    if isinstance(job.payload, dict):
        cand = job.payload.get("candidate_id")
        if cand:
            try:
                cand_uuid = uuid.UUID(cand)
                from app.models.candidate import Candidate
                if db.get(Candidate, cand_uuid) is not None:
                    candidate_id = cand_uuid
            except (TypeError, ValueError):
                candidate_id = None

    clip = Clip(
        campaign_id=asset.campaign_id,
        asset_id=asset.id,
        candidate_id=candidate_id,
        render_job_id=job.id,
        file_path=result_data.get("file_path"),
        duration_seconds=result_data.get("duration_seconds"),
        file_size=result_data.get("file_size"),
        qa_status=ClipQAStatus.PENDING.value,
        qa_result={},
        status=ClipStatus.CREATED.value,
    )
    db.add(clip)
    db.commit()
    db.refresh(clip)

    qa_job: Optional[Job] = None
    try:
        qa_rules: dict[str, Any] = {}
        try:
            from app.models.campaign import Campaign
            from app.services.rules.runtime import qa_rules as _campaign_qa_rules

            campaign = db.get(Campaign, asset.campaign_id)
            if campaign is not None:
                qa_rules = _campaign_qa_rules(campaign)  # #35: duration + 1080x1920/fps/audio/codec
        except Exception as e:  # noqa: BLE001
            logger.warning("could not load QA rules from campaign %s: %s", asset.campaign_id, e)

        qa_payload: dict[str, Any] = {
            "clip_id": str(clip.id),
            "asset_id": str(asset.id),
            "campaign_id": str(asset.campaign_id),
            "file_path": clip.file_path,
        }
        if qa_rules:
            qa_payload["rules"] = qa_rules
        qa_job = create_job(
            db, job_type="qa", payload=qa_payload, priority=job.priority,
            max_attempts=job.max_attempts, affinity_worker_id=job.worker_id,
        )
        clip.qa_job_id = qa_job.id
        db.commit()
        db.refresh(clip)
    except Exception as e:  # noqa: BLE001
        logger.exception("failed to auto-create qa job: %s", e)
    return clip, qa_job


def on_qa_completed(db: Session, job: Job, result_data: dict) -> Optional[Clip]:
    clip_id = job.payload.get("clip_id") if isinstance(job.payload, dict) else None
    if not clip_id:
        return None
    try:
        clip = db.get(Clip, uuid.UUID(clip_id))
    except (TypeError, ValueError):
        return None
    if clip is None:
        return None

    raw_status = (result_data.get("status") or "review").lower()
    if raw_status in ("pass", "passed", "ok"):
        qa_status = ClipQAStatus.PASS.value
    elif raw_status in ("fail", "failed", "error"):
        qa_status = ClipQAStatus.FAIL.value
    else:
        qa_status = ClipQAStatus.REVIEW.value
    clip.qa_status = qa_status
    clip.qa_result = result_data
    clip.qa_at = _now()
    if qa_status == ClipQAStatus.PASS.value:
        clip.status = ClipStatus.APPROVED.value
    elif qa_status == ClipQAStatus.FAIL.value:
        clip.status = ClipStatus.REJECTED.value
    else:
        clip.status = ClipStatus.REVIEW.value
    clip.qa_job_id = job.id
    # #43: post-render rules verifier (publish requires compliance 'pass').
    try:
        from app.services.rules.verifier import verify_clip

        verify_clip(db, clip)
    except Exception as e:  # noqa: BLE001
        logger.exception("clip %s compliance verification failed: %s", clip.id, e)
    db.commit()
    db.refresh(clip)
    if qa_status == ClipQAStatus.PASS.value:
        try:
            from app.services.clip_storage_service import set_clip_location
            set_clip_location(db, clip.id, "pending_upload", final_path_worker=(result_data or {}).get("final_path_worker"))
        except Exception as e:  # noqa: BLE001
            logger.exception("clip %s step18 failed: %s", clip.id, e)
    return clip


TERMINAL_ERROR_KINDS = frozenset({
    "not_a_video", "youtube_channel", "youtube_playlist", "instagram_profile",
    "instagram_story_unavailable", "instagram_post_unavailable", "tiktok_account",
    "channel_page", "404_not_found", "403_forbidden", "external_404",
    "external_non_video", "page_not_video", "html_instead_of_media", "media_too_small",
})


def _classify_job_error(job: Job) -> tuple[str | None, str | None]:
    result = job.result if isinstance(job.result, dict) else {}
    err = result.get("error") if isinstance(result.get("error"), dict) else result
    kind = err.get("kind") if isinstance(err, dict) else None
    detail = err.get("detail") if isinstance(err, dict) else None
    if not kind and job.error_message:
        em = str(job.error_message)
        for k in TERMINAL_ERROR_KINDS:
            if em.startswith(k):
                return k, em
        if getattr(job, "job_type", None) == "download":
            for k in PERMANENT_DOWNLOAD_KINDS:
                if em.startswith(f"{k}:") or em == k:
                    return k, em
            found = permanent_download_kind(em)
            if found:
                return found, em
    return (str(kind) if kind else None, detail)


_SKIP_ASSET_KINDS = frozenset({
    "drive_folder", "dropbox_folder", "brand_asset",
    "youtube_profile", "twitter_profile", "tiktok_profile",
    "instagram_profile", "profile",
})


def _asset_can_still_produce(asset) -> bool:
    """True when this asset can still become a clip."""
    status = getattr(asset, "status", None)
    if status in (AssetStatus.DOWNLOADED.value, AssetStatus.TRANSCRIBED.value):
        return True
    if status != AssetStatus.PENDING.value:
        return False
    if getattr(asset, "asset_type", None) in {"folder", "brand_asset"}:
        return False
    meta = asset.extra_metadata or {}
    if meta.get("skip_download") is True:
        return False
    if meta.get("kind") in _SKIP_ASSET_KINDS:
        return False
    from app.api.campaigns import _is_real_video_url
    return _is_real_video_url(getattr(asset, "source_url", None))


def _maybe_mark_campaign_no_assets(db: Session, asset: Asset) -> None:
    """Park a scored campaign whose videos all failed permanently.

    Downloaded and transcribed assets keep the campaign scored so transcribe
    and clip selection continue. The visible status is the existing
    `blocked_no_assets` (there is no separate `no_assets` enum). The reason
    is stored as no_assets for the dashboard history.
    """
    from app.services.campaign_transitions import record_auto_transition

    campaign = db.get(Campaign, asset.campaign_id)
    if campaign is None or campaign.status != CampaignStatus.SCORED.value:
        return
    assets = db.query(Asset).filter(Asset.campaign_id == asset.campaign_id).all()
    if any(_asset_can_still_produce(item) for item in assets):
        return
    inflight = (
        db.query(Job)
        .filter(Job.job_type == "download")
        .filter(Job.status.in_(("pending", "assigned", "processing")))
        .all()
    )
    cid = str(asset.campaign_id)
    for other in inflight:
        if getattr(other, "job_type", None) not in (None, "download"):
            continue
        if getattr(other, "status", None) not in ("pending", "assigned", "processing"):
            continue
        payload = other.payload if isinstance(getattr(other, "payload", None), dict) else {}
        if str(payload.get("campaign_id") or "") == cid:
            return
    record_auto_transition(
        campaign,
        CampaignStatus.BLOCKED_NO_ASSETS.value,
        reason="no_assets: every downloadable video failed permanently",
        actor="download_fail",
    )
    meta = dict(campaign.source_metadata or {})
    meta["no_assets"] = {
        "reason": "permanent_download_failure",
        "at": _now().isoformat(),
    }
    campaign.source_metadata = meta
    logger.info(
        "campaign %s -> blocked_no_assets (no downloadable videos left)",
        asset.campaign_id,
    )


def on_job_failed(db: Session, job: Job) -> None:
    logger.warning(
        "terminal job failure %s error=%s",
        format_ids(**ids_from_job(job)),
        redact(job.error_message or ""),
    )
    asset_id = _asset_id_from_payload(job.payload or {})
    if not asset_id:
        return
    asset = db.get(Asset, uuid.UUID(asset_id))
    if asset is None:
        return
    if asset.status not in (AssetStatus.PENDING.value, AssetStatus.DOWNLOADED.value):
        logger.info(
            "job failure left asset status=%s %s",
            asset.status,
            format_ids(**ids_from_job(job)),
        )
        return
    error_kind, error_detail = _classify_job_error(job)
    asset.status = AssetStatus.FAILED.value
    meta = dict(asset.extra_metadata or {})
    permanent_download = error_kind in PERMANENT_DOWNLOAD_KINDS
    if error_kind and (error_kind in TERMINAL_ERROR_KINDS or permanent_download):
        meta["skip_download"] = True
        if permanent_download:
            meta["unavailable"] = True
        meta["last_error_kind"] = error_kind
        meta["last_error_detail"] = redact(error_detail or "")[:500]
        meta["terminal_failed_at"] = _now().isoformat()
    else:
        # A transcribe (or download) that died for a non-terminal reason used
        # to clear any previous kind and leave no explanation. Keep the reason
        # so a stalled asset can be seen and requeued on purpose.
        meta.pop("skip_download", None)
        meta["last_error_kind"] = (error_kind or f"{job.job_type}_failed")[:64]
        detail = error_detail or job.error_message or ""
        meta["last_error_detail"] = redact(str(detail))[:500]
        meta["terminal_failed_at"] = _now().isoformat()
    asset.extra_metadata = meta
    if permanent_download and getattr(job, "job_type", None) == "download":
        _maybe_mark_campaign_no_assets(db, asset)
    db.commit()
