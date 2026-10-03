"""Mission Control — read-only dashboard endpoints.

Strict rules (do not change):
  * Read-only: no UPDATE / INSERT / DELETE on any table exposed here.
  * Reuses `require_bearer` (same Bearer token as the rest of the API).
  * If `settings.mission_control_enabled` is False, the router still mounts
    but every endpoint returns 404, so a casual probe cannot enumerate it.
  * LIMITs are clamped at the maximum allowed (500) in Python.
"""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.auth import require_bearer
from app.config import settings
from app.db.database import SessionLocal
from app.schemas.mission_control import (
    CandidatesOut,
    CampaignDetailOut,
    CampaignListOut,
    CampaignRulesOut,
    ClipsOut,
    JobRecentOut,
    OverviewOut,
    PipelineOut,
    VideosOut,
)


router = APIRouter(prefix="/mission-control", tags=["mission-control"])


# --- Gate --------------------------------------------------------------------

def _enabled_or_404() -> bool:
    """Return True if the feature flag is on; raise 404 otherwise.

    Why a feature flag returning 404 (not 403): the dashboard is opt-in.
    We don't want to leak the existence of the endpoints when disabled.
    """
    if not settings.mission_control_enabled:
        raise HTTPException(status_code=404, detail="not found")
    return True


def _get_db() -> Session:
    """Direct session — we don't go through get_db() because we want a
    plain Session, not a generator, so we can call .close() in finally."""
    return SessionLocal()


# --- Helpers ----------------------------------------------------------------

def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _clamp_limit(n: int, default: int = 100, maximum: int = 500) -> int:
    if n is None or n <= 0:
        return default
    return min(n, maximum)


def _num(v: Any) -> Optional[float]:
    """Best-effort float for JSONB values written by different ticks."""
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _norm_error(err: Any) -> Optional[Dict[str, Any]]:
    """briefing_error / resolve_error -> {kind, message, at} (or None).

    3a writes {kind, message, at}; 3b writes {kind, message} (no `at`);
    very old rows hold a bare string.
    """
    if isinstance(err, dict):
        return {
            "kind": err.get("kind"),
            "message": err.get("message"),
            "at": err.get("at"),
        }
    if err:
        return {"kind": "legacy_string", "message": str(err), "at": None}
    return None


def _score_fields(sm: Dict[str, Any]) -> Dict[str, Any]:
    """Score written by campaign_scorer_tick (3c) in source_metadata.score:

        {value, real_assets, cpm_usd, prize_pool_usd,
         breakdown: {value, base, penalties, penalty_total, min_to_run, eligible}}

    Older code looked for score.total / score.priority / flat priority_score,
    which no tick writes; they are kept only as fallbacks.
    """
    score_obj = sm.get("score") if isinstance(sm.get("score"), dict) else {}
    breakdown = score_obj.get("breakdown") if isinstance(score_obj.get("breakdown"), dict) else None
    value = score_obj.get("value")
    if value is None:
        value = score_obj.get("total")
    if value is None:
        value = sm.get("priority_score")
    tier = sm.get("priority_tier")
    if tier is None:
        tier = score_obj.get("priority")
    return {
        "priority_score": _num(value),
        "priority_tier": _num(tier),
        "priority_tie_break": score_obj.get("tie_break"),
        "priority_breakdown": breakdown,
        "priority_rank_reason": score_obj.get("rank_reason"),
        "score_eligible": (breakdown or {}).get("eligible"),
        "score_min_to_run": _num((breakdown or {}).get("min_to_run")),
    }


def _llm_summary(db: Session, where: str = "1=1", params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Aggregate llm_usage rows (migration 0015). `where` is a static SQL
    fragment built in this module only (never user input)."""
    params = params or {}
    rows = db.execute(
        text(
            f"""
            SELECT stage, COUNT(*) AS calls,
                   COUNT(*) FILTER (WHERE NOT ok) AS errors,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(SUM(cost_usd), 0) AS cost_usd,
                   MAX(created_at) AS last_at
            FROM llm_usage
            WHERE {where}
            GROUP BY stage
            ORDER BY stage
            """
        ),
        params,
    ).all()
    by_stage = [
        {
            "stage": r.stage,
            "calls": int(r.calls),
            "errors": int(r.errors),
            "total_tokens": int(r.total_tokens),
            "cost_usd": float(r.cost_usd),
        }
        for r in rows
    ]
    last = max((r.last_at for r in rows if r.last_at is not None), default=None)
    return {
        "calls": sum(b["calls"] for b in by_stage),
        "errors": sum(b["errors"] for b in by_stage),
        "total_tokens": sum(b["total_tokens"] for b in by_stage),
        "cost_usd": round(sum(b["cost_usd"] for b in by_stage), 6),
        "by_stage": by_stage,
        "last_call_at": _iso(last),
    }


def _campaign_name_for_job(db: Session, payload: dict) -> Optional[str]:
    """Look up campaign name from a job's payload['campaign_id'] (best effort)."""
    cid = payload.get("campaign_id") if isinstance(payload, dict) else None
    if cid is None:
        return None
    try:
        cid_int = int(cid)
    except (TypeError, ValueError):
        return None
    row = db.execute(
        text("SELECT name FROM campaigns WHERE id = :id"),
        {"id": cid_int},
    ).first()
    return row[0] if row else None


# --- 1. Overview ------------------------------------------------------------

@router.get("/overview", response_model=OverviewOut)
def overview(
    _a: bool = Depends(_enabled_or_404),
    _b: bool = Depends(require_bearer),
):
    """Global summary: counts by status, 24h rates, disk-unavailable count."""
    db = _get_db()
    try:
        # Campaigns by status
        camp_rows = db.execute(
            text("SELECT status, COUNT(*) FROM campaigns GROUP BY status")
        ).all()
        campaigns_by_status = {row[0]: row[1] for row in camp_rows}

        # Jobs by status × job_type (last 24h for the "active" view)
        job_rows = db.execute(
            text(
                """
                SELECT job_type, status, COUNT(*)
                FROM jobs
                WHERE created_at >= NOW() - INTERVAL '24 hours'
                GROUP BY job_type, status
                """
            )
        ).all()
        jobs_by_type_status: Dict[str, Dict[str, int]] = {}
        for jt, st, n in job_rows:
            jobs_by_type_status.setdefault(jt, {})[st] = n

        # Assets by status
        asset_rows = db.execute(
            text("SELECT status, COUNT(*) FROM assets GROUP BY status")
        ).all()
        assets_by_status = {row[0]: row[1] for row in asset_rows}

        # Clips by qa_status
        clip_rows = db.execute(
            text(
                "SELECT qa_status, COUNT(*) FROM clips GROUP BY qa_status"
            )
        ).all()
        clips_by_qa_status = {row[0]: row[1] for row in clip_rows}

        # Clips by top-level status (approved/rejected/published)
        clip_status_rows = db.execute(
            text("SELECT status, COUNT(*) FROM clips GROUP BY status")
        ).all()
        clips_by_status = {row[0]: row[1] for row in clip_status_rows}

        total_campaigns = sum(campaigns_by_status.values())
        total_jobs_last_24h = sum(sum(v.values()) for v in jobs_by_type_status.values())
        total_clips_last_24h = db.execute(
            text(
                "SELECT COUNT(*) FROM clips WHERE created_at >= NOW() - INTERVAL '24 hours'"
            )
        ).scalar_one()

        # Disk-unavailable videos: assets with no local_path and not pending
        disk_unavailable_videos = db.execute(
            text(
                """
                SELECT COUNT(*) FROM assets
                WHERE local_path IS NULL AND status != 'pending'
                """
            )
        ).scalar_one()

        # Recent errors (last 5, with error_message)
        recent_errors = db.execute(
            text(
                """
                SELECT id, job_type, error_message, created_at
                FROM jobs
                WHERE error_message IS NOT NULL
                ORDER BY created_at DESC
                LIMIT 5
                """
            )
        ).all()
        recent_errors_list = [
            {
                "id": str(r[0]),
                "job_type": r[1],
                "error_message": r[2][:500] if r[2] else None,
                "created_at": _iso(r[3]),
            }
            for r in recent_errors
        ]

        return {
            "generated_at": _iso(datetime.now(timezone.utc)),
            "total_campaigns": total_campaigns,
            "total_jobs_last_24h": total_jobs_last_24h,
            "total_clips_last_24h": total_clips_last_24h,
            "disk_unavailable_videos": disk_unavailable_videos,
            "campaigns_by_status": campaigns_by_status,
            "jobs_by_type_status": jobs_by_type_status,
            "assets_by_status": assets_by_status,
            "clips_by_qa_status": clips_by_qa_status,
            "clips_by_status": clips_by_status,
            "recent_errors": recent_errors_list,
            "llm_usage_24h": _llm_summary(db, "created_at >= NOW() - INTERVAL '24 hours'"),
            "llm_usage_total": _llm_summary(db),
            "worker_file_base_url": settings.worker_file_base_url or None,
        }
    finally:
        db.close()


# --- 2. Campaigns list (enriched) ------------------------------------------

@router.get("/campaigns", response_model=CampaignListOut)
def campaigns_list(
    _a: bool = Depends(_enabled_or_404),
    _b: bool = Depends(require_bearer),
    limit: int = Query(100, ge=1),
):
    """One row per campaign with denormalized counters (assets, clips, QA pass)."""
    db = _get_db()
    try:
        rows = db.execute(
            text(
                """
                SELECT
                    c.id, c.name, c.status, c.source_provider, c.source_url,
                    c.source_id, c.source_metadata, c.spec,
                    c.assets_count, c.clips_approved, c.clips_published,
                    c.created_at, c.updated_at,
                    COUNT(DISTINCT a.id) AS assets_total,
                    COUNT(DISTINCT CASE WHEN a.status = 'transcribed' THEN a.id END)
                        AS assets_transcribed,
                    COUNT(DISTINCT cl.id) AS clips_total,
                    COUNT(DISTINCT CASE WHEN cl.qa_status = 'pass' THEN cl.id END)
                        AS clips_approved_qa
                FROM campaigns c
                LEFT JOIN assets a ON a.campaign_id = c.id
                LEFT JOIN clips  cl ON cl.campaign_id = c.id
                GROUP BY c.id
                ORDER BY c.updated_at DESC
                LIMIT :lim
                """
            ),
            {"lim": _clamp_limit(limit)},
        ).all()

        items = []
        for r in rows:
            sm = r.source_metadata or {}
            # El scorer escribe el score en source_metadata.score (sub-objeto):
            #   { total, priority, tie_break, breakdown, rank_reason }
            # El endpoint legacy buscaba priority_score / priority_tier planos
            # que el cron nunca escribía. Mantenemos las keys planas como
            # fallback por si en el futuro alguien rellena esos campos, pero la
            # fuente de verdad es el sub-objeto `score`.
            items.append(
                {
                    "id": r.id,
                    "name": r.name,
                    "status": r.status,
                    "source_provider": r.source_provider,
                    "source_url": r.source_url,
                    "source_id": r.source_id,
                    "source_metadata": r.source_metadata,
                    "spec": r.spec,
                    "assets_count": r.assets_count,
                    "assets_total": r.assets_total,
                    "assets_transcribed": r.assets_transcribed,
                    "clips_total": r.clips_total,
                    "clips_approved": r.clips_approved,
                    "clips_approved_qa": r.clips_approved_qa,
                    "clips_published": r.clips_published,
                    **_score_fields(sm),
                    "briefing_error": _norm_error(sm.get("briefing_error")),
                    "resolve_error": _norm_error(sm.get("resolve_error")),
                    "created_at": _iso(r.created_at),
                    "updated_at": _iso(r.updated_at),
                }
            )
        return {"items": items, "count": len(items)}
    finally:
        db.close()


# --- 3. Campaign detail (drill-down) ---------------------------------------

@router.get("/campaigns/{campaign_id}", response_model=CampaignDetailOut)
def campaign_detail(
    campaign_id: int,
    _a: bool = Depends(_enabled_or_404),
    _b: bool = Depends(require_bearer),
):
    db = _get_db()
    try:
        # 1) campaign
        c = db.execute(
            text(
                """
                SELECT id, name, status, source_provider, source_url, source_id,
                       source_metadata, source_instructions, spec,
                       assets_count, clips_approved, clips_published,
                       created_at, updated_at
                FROM campaigns
                WHERE id = :id
                """
            ),
            {"id": campaign_id},
        ).first()
        if c is None:
            raise HTTPException(status_code=404, detail="Campaign not found")

        sm = c.source_metadata or {}
        # Errores persistidos por el brief-reader / resolver. Forma estable:
        #   { "kind": "<short_tag>", "message": "<human>", "at": "<iso>" }
        # (Los registros viejos pueden tener solo string suelto; lo soportamos.)
        briefing_error_out = _norm_error(sm.get("briefing_error"))
        resolve_error_out = _norm_error(sm.get("resolve_error"))
        campaign = {
            "id": c.id,
            "name": c.name,
            "status": c.status,
            "source_provider": c.source_provider,
            "source_url": c.source_url,
            "source_id": c.source_id,
            "source_metadata": c.source_metadata,
            "source_instructions": c.source_instructions,
            "spec": c.spec,
            "assets_count": c.assets_count,
            "clips_approved": c.clips_approved,
            "clips_published": c.clips_published,
            "briefing_error": briefing_error_out,
            "resolve_error": resolve_error_out,
            "created_at": _iso(c.created_at),
            "updated_at": _iso(c.updated_at),
        }

        # 2) assets
        a_rows = db.execute(
            text(
                """
                SELECT id, source_url, source_provider, asset_type, status,
                       local_path, file_size, duration_seconds, sha256,
                       mime_type, extra_metadata, downloaded_at, transcribed_at,
                       created_at
                FROM assets
                WHERE campaign_id = :id
                ORDER BY created_at ASC
                """
            ),
            {"id": campaign_id},
        ).all()
        assets = [
            {
                "id": str(a.id),
                "source_url": a.source_url,
                "source_provider": a.source_provider,
                "asset_type": a.asset_type,
                "status": a.status,
                "local_path": a.local_path,
                "file_size": a.file_size,
                "duration_seconds": a.duration_seconds,
                "sha256": a.sha256,
                "mime_type": a.mime_type,
                "extra_metadata": a.extra_metadata or {},
                "downloaded_at": _iso(a.downloaded_at),
                "transcribed_at": _iso(a.transcribed_at),
                "created_at": _iso(a.created_at),
            }
            for a in a_rows
        ]

        # 3) active jobs (pending/assigned/processing) for this campaign
        j_rows = db.execute(
            text(
                """
                SELECT id, job_type, status, priority, attempts, max_attempts,
                       worker_id, error_message, created_at, updated_at,
                       started_at, completed_at, payload, result
                FROM jobs
                WHERE status IN ('pending', 'assigned', 'processing')
                  AND payload->>'campaign_id' = :cid
                ORDER BY created_at ASC
                """
            ),
            {"cid": str(campaign_id)},
        ).all()
        active_jobs = [
            {
                "id": str(j.id),
                "job_type": j.job_type,
                "status": j.status,
                "priority": j.priority,
                "attempts": j.attempts,
                "max_attempts": j.max_attempts,
                "worker_id": j.worker_id,
                "error_message": (j.error_message[:300] if j.error_message else None),
                "created_at": _iso(j.created_at),
                "updated_at": _iso(j.updated_at),
                "started_at": _iso(j.started_at),
                "completed_at": _iso(j.completed_at),
                "payload": j.payload,
                "result": j.result,
            }
            for j in j_rows
        ]

        # 4) clips
        cl_rows = db.execute(
            text(
                """
                SELECT id, asset_id, file_path, duration_seconds, file_size,
                       qa_status, qa_result, status, location, final_path_worker,
                       created_at, updated_at, qa_at, published_at,
                       publish_approved_at
                FROM clips
                WHERE campaign_id = :id
                ORDER BY created_at DESC
                LIMIT 200
                """
            ),
            {"id": campaign_id},
        ).all()
        clips = [
            {
                "id": str(cl.id),
                "asset_id": str(cl.asset_id),
                "file_path": cl.file_path,
                "duration_seconds": cl.duration_seconds,
                "file_size": cl.file_size,
                "qa_status": cl.qa_status,
                "qa_result": cl.qa_result,
                "status": cl.status,
                "location": cl.location,
                "final_path_worker": cl.final_path_worker,
                "created_at": _iso(cl.created_at),
                "updated_at": _iso(cl.updated_at),
                "qa_at": _iso(cl.qa_at),
                "published_at": _iso(cl.published_at),
                "publish_approved_at": _iso(cl.publish_approved_at),
            }
            for cl in cl_rows
        ]

        return {
            "campaign": campaign,
            "assets": assets,
            "active_jobs": active_jobs,
            "clips": clips,
            "llm_usage": _llm_summary(db, "campaign_id = :cid", {"cid": campaign_id}),
            "worker_file_base_url": settings.worker_file_base_url or None,
        }
    finally:
        db.close()


# --- 3b. Campaign rules (read-only, saneado para UI) ---------------------

@router.get("/campaigns/{campaign_id}/rules", response_model=CampaignRulesOut)
def campaign_rules(
    campaign_id: int,
    _a: bool = Depends(_enabled_or_404),
    _b: bool = Depends(require_bearer),
):
    """Devuelve spec + source_metadata saneado para mostrar la tab 'Reglas'.

    Por qué un endpoint aparte (no meter todo en /campaigns/{id}):
    - El JSONB crudo puede ser pesado y no lo queremos en cada drill-down.
    - Aquí normalizamos los campos que el frontend sabe pintar (rules,
      discovered, priority_components, asset_links_brief) y los envolvemos
    en un shape estable aunque la BD evolucione.
    """
    db = _get_db()
    try:
        row = db.execute(
            text(
                """
                SELECT id, name, status, source_provider, source_url,
                       source_metadata, spec
                FROM campaigns
                WHERE id = :id
                """
            ),
            {"id": campaign_id},
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail="Campaign not found")

        sm = row.source_metadata or {}
        discovered = sm.get("discovered") or {}

        # Reglas estructuradas (puede venir {} o vacío)
        rules = sm.get("rules") or {}

        # Texto crudo del briefing (lo que el LLM vio para sacar las reglas)
        raw = discovered.get("raw") or {}
        card_text = raw.get("card_text") or discovered.get("description") or ""

        # Links de assets que dijo el briefing que había (Drive, YouTube, etc.)
        # — distinto de los assets ya resueltos en BD.
        asset_links_brief = sm.get("asset_links_brief") or sm.get("reference_materials") or []
        asset_links_raw = discovered.get("asset_links") or sm.get("asset_links") or []

        # Normalizar asset_links_raw a una lista de strings simples
        asset_links_normalized: List[str] = []
        for item in asset_links_raw:
            if isinstance(item, str):
                asset_links_normalized.append(item)
            elif isinstance(item, dict):
                # por si vienen como {url, kind, label}
                if item.get("url"):
                    asset_links_normalized.append(item["url"])

        # Drive IDs extraídos (lo que el resolver debería haber bajado)
        drive_ids = []
        for url in asset_links_normalized:
            if "drive.google.com" in url:
                # /file/d/<id>/ o ?id=<id>
                m = re.search(r"/file/d/([A-Za-z0-9_-]+)", url)
                if not m:
                    m = re.search(r"[?&]id=([A-Za-z0-9_-]+)", url)
                if m:
                    drive_ids.append(m.group(1))

        # Componentes de prioridad (cómo se calculó el score)
        # Cómo se calculó el score: el scorer (3c) escribe score.breakdown;
        # priority_components es una clave legacy que ningún tick escribe.
        score_f = _score_fields(sm)
        priority_components = sm.get("priority_components") or score_f["priority_breakdown"] or {}
        score_obj = sm.get("score") if isinstance(sm.get("score"), dict) else None
        score_preview = sm.get("score_preview") if isinstance(sm.get("score_preview"), dict) else None
        brief_docs = sm.get("brief_docs") if isinstance(sm.get("brief_docs"), list) else []

        # Specs — el "spec" canónico. Si está vacío, marcamos explícito.
        spec = row.spec or {}
        spec_is_empty = (not spec) or (spec == {}) or (len(spec.keys()) == 0)

        return {
            "campaign_id": row.id,
            "campaign_name": row.name,
            "status": row.status,
            "source_provider": row.source_provider,
            "source_url": row.source_url,
            "spec": spec,
            "spec_is_empty": spec_is_empty,
            "rules": rules,
            "card_text": card_text,
            "discovered": {
                "name": discovered.get("name"),
                "external_id": (
                    str(discovered["external_id"])
                    if discovered.get("external_id") is not None else None
                ),
                "detail_url": discovered.get("detail_url"),
                "cpm_usd_per_1k": _num(discovered.get("cpm_usd_per_1k")),
                "prize_pool_usd": _num(discovered.get("prize_pool_usd")),
                "joined": discovered.get("joined"),
            },
            "asset_links_brief": asset_links_brief,
            "asset_links_raw": asset_links_normalized,
            "asset_links_count": len(asset_links_normalized),
            "drive_ids": drive_ids,
            "brief_docs": brief_docs,
            "score": score_obj,
            "score_preview": score_preview,
            "priority_tier": score_f["priority_tier"],
            "priority_score": score_f["priority_score"],
            "priority_components": priority_components,
            "briefed_at": sm.get("briefed_at"),
            "joined": sm.get("joined"),
            "cpm_usd_per_1k": _num(sm.get("cpm_usd_per_1k")),
            "prize_pool_usd": _num(sm.get("prize_pool_usd")),
        }
    finally:
        db.close()


# --- 4. Jobs recent ---------------------------------------------------------

@router.get("/jobs/recent", response_model=JobRecentOut)
def jobs_recent(
    _a: bool = Depends(_enabled_or_404),
    _b: bool = Depends(require_bearer),
    limit: int = Query(100, ge=1),
    job_type: Optional[str] = Query(None, max_length=64),
    status_filter: Optional[str] = Query(None, alias="status", max_length=32),
):
    """Most-recent jobs with derived `campaign_name` and `elapsed_seconds`."""
    db = _get_db()
    try:
        # Note: we use a CTE that joins on payload->>'campaign_id' cast to int;
        # jobs with non-numeric or missing campaign_id simply get NULL name.
        params: Dict[str, Any] = {"lim": _clamp_limit(limit, default=100)}
        where_extra = ""
        if job_type:
            where_extra += " AND j.job_type = :jt"
            params["jt"] = job_type
        if status_filter:
            where_extra += " AND j.status = :st"
            params["st"] = status_filter

        rows = db.execute(
            text(
                f"""
                SELECT
                    j.id, j.job_type, j.status, j.priority, j.attempts,
                    j.max_attempts, j.worker_id, j.error_message,
                    j.payload, j.result,
                    j.created_at, j.updated_at, j.started_at, j.completed_at,
                    c.name AS campaign_name,
                    EXTRACT(
                        EPOCH FROM (COALESCE(j.completed_at, NOW()) - j.created_at)
                    )::float AS elapsed_seconds
                FROM jobs j
                LEFT JOIN campaigns c
                  ON c.id = NULLIF(j.payload->>'campaign_id', '')::int
                WHERE 1=1 {where_extra}
                ORDER BY j.updated_at DESC
                LIMIT :lim
                """
            ),
            params,
        ).all()

        items = [
            {
                "id": str(j.id),
                "job_type": j.job_type,
                "status": j.status,
                "priority": j.priority,
                "attempts": j.attempts,
                "max_attempts": j.max_attempts,
                "worker_id": j.worker_id,
                "error_message": (j.error_message[:500] if j.error_message else None),
                "payload": j.payload,
                "result": j.result,
                "campaign_name": j.campaign_name,
                "created_at": _iso(j.created_at),
                "updated_at": _iso(j.updated_at),
                "started_at": _iso(j.started_at),
                "completed_at": _iso(j.completed_at),
                "elapsed_seconds": float(j.elapsed_seconds or 0.0),
            }
            for j in rows
        ]
        return {"items": items, "count": len(items)}
    finally:
        db.close()


# --- 5. Pipeline view for one campaign --------------------------------------

@router.get("/pipeline/{campaign_id}", response_model=PipelineOut)
def pipeline_view(
    campaign_id: int,
    _a: bool = Depends(_enabled_or_404),
    _b: bool = Depends(require_bearer),
):
    """For each asset of the campaign, the latest job per pipeline stage."""
    db = _get_db()
    try:
        # Verify campaign exists (404 fast if not).
        exists = db.execute(
            text("SELECT 1 FROM campaigns WHERE id = :id"),
            {"id": campaign_id},
        ).first()
        if exists is None:
            raise HTTPException(status_code=404, detail="Campaign not found")

        # Per asset, latest job per job_type.
        rows = db.execute(
            text(
                """
                WITH ranked AS (
                    SELECT
                        a.id AS asset_id,
                        a.source_url,
                        a.status AS asset_status,
                        j.id AS job_id,
                        j.job_type,
                        j.status,
                        j.created_at,
                        j.started_at,
                        j.completed_at,
                        j.error_message,
                        ROW_NUMBER() OVER (
                            PARTITION BY a.id, j.job_type
                            ORDER BY j.created_at DESC
                        ) AS rn
                    FROM assets a
                    LEFT JOIN jobs j
                      ON j.payload->>'asset_id' = a.id::text
                    WHERE a.campaign_id = :cid
                      AND j.job_type IN ('download', 'transcribe', 'render', 'qa')
                )
                SELECT asset_id, source_url, asset_status,
                       job_id, job_type, status,
                       created_at, started_at, completed_at, error_message
                FROM ranked
                WHERE rn = 1
                ORDER BY asset_id, job_type
                """
            ),
            {"cid": campaign_id},
        ).all()

        # Group by asset
        per_asset: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            aid = str(r.asset_id)
            bucket = per_asset.setdefault(
                aid,
                {
                    "asset_id": aid,
                    "source_url": r.source_url,
                    "asset_status": r.asset_status,
                    "stages": {},
                },
            )
            if r.job_id is not None:
                bucket["stages"][r.job_type] = {
                    "job_id": str(r.job_id),
                    "status": r.status,
                    "created_at": _iso(r.created_at),
                    "started_at": _iso(r.started_at),
                    "completed_at": _iso(r.completed_at),
                    "error_message": (r.error_message[:300] if r.error_message else None),
                }

        return {
            "campaign_id": campaign_id,
            "assets": list(per_asset.values()),
        }
    finally:
        db.close()


# --- 6. Videos inventory ----------------------------------------------------

@router.get("/videos", response_model=VideosOut)
def videos_inventory(
    _a: bool = Depends(_enabled_or_404),
    _b: bool = Depends(require_bearer),
    limit: int = Query(200, ge=1),
):
    """Downloaded/transcribed assets with their local_path (raw text, not a URL)."""
    db = _get_db()
    try:
        rows = db.execute(
            text(
                """
                SELECT a.id, a.source_url, a.local_path, a.file_size,
                       a.duration_seconds, a.status,
                       a.downloaded_at, a.transcribed_at,
                       c.name AS campaign_name,
                       c.id AS campaign_id
                FROM assets a
                JOIN campaigns c ON c.id = a.campaign_id
                WHERE a.status IN ('downloaded', 'transcribed')
                ORDER BY a.downloaded_at DESC NULLS LAST
                LIMIT :lim
                """
            ),
            {"lim": _clamp_limit(limit, default=200)},
        ).all()
        items = [
            {
                "id": str(r.id),
                "campaign_id": r.campaign_id,
                "campaign_name": r.campaign_name,
                "source_url": r.source_url,
                "local_path": r.local_path,
                "file_size": r.file_size,
                "duration_seconds": r.duration_seconds,
                "status": r.status,
                "downloaded_at": _iso(r.downloaded_at),
                "transcribed_at": _iso(r.transcribed_at),
            }
            for r in rows
        ]
        return {
            "items": items,
            "count": len(items),
            "worker_file_base_url": settings.worker_file_base_url or None,
        }
    finally:
        db.close()


# --- 7. Clips inventory -----------------------------------------------------

@router.get("/clips", response_model=ClipsOut)
def clips_inventory(
    _a: bool = Depends(_enabled_or_404),
    _b: bool = Depends(require_bearer),
    campaign_id: Optional[int] = Query(None),
    qa_status: Optional[str] = Query(None, max_length=32),
    limit: int = Query(200, ge=1),
):
    db = _get_db()
    try:
        params: Dict[str, Any] = {"lim": _clamp_limit(limit, default=200)}
        where = "1=1"
        if campaign_id is not None:
            where += " AND cl.campaign_id = :cid"
            params["cid"] = campaign_id
        if qa_status:
            where += " AND cl.qa_status = :qs"
            params["qs"] = qa_status
        rows = db.execute(
            text(
                f"""
                SELECT cl.id, cl.campaign_id, cl.asset_id, cl.candidate_id,
                       cl.render_job_id, cl.qa_job_id,
                       cl.file_path, cl.duration_seconds, cl.file_size,
                       cl.qa_status, cl.qa_result, cl.status,
                       cl.location, cl.final_path_worker,
                       cl.created_at, cl.updated_at, cl.qa_at, cl.published_at,
                       cl.publish_approved_at,
                       c.name AS campaign_name,
                       a.source_url AS asset_source_url
                FROM clips cl
                LEFT JOIN campaigns c ON c.id = cl.campaign_id
                LEFT JOIN assets a ON a.id = cl.asset_id
                WHERE {where}
                ORDER BY cl.created_at DESC
                LIMIT :lim
                """
            ),
            params,
        ).all()
        items = [
            {
                "id": str(r.id),
                "campaign_id": r.campaign_id,
                "campaign_name": r.campaign_name,
                "asset_id": str(r.asset_id) if r.asset_id else None,
                "asset_source_url": r.asset_source_url,
                "candidate_id": str(r.candidate_id) if r.candidate_id else None,
                "render_job_id": str(r.render_job_id) if r.render_job_id else None,
                "qa_job_id": str(r.qa_job_id) if r.qa_job_id else None,
                "file_path": r.file_path,
                "duration_seconds": r.duration_seconds,
                "file_size": r.file_size,
                "qa_status": r.qa_status,
                "qa_result": r.qa_result,
                "status": r.status,
                "location": r.location,
                "final_path_worker": r.final_path_worker,
                "created_at": _iso(r.created_at),
                "updated_at": _iso(r.updated_at),
                "qa_at": _iso(r.qa_at),
                "published_at": _iso(r.published_at),
                "publish_approved_at": _iso(r.publish_approved_at),
            }
            for r in rows
        ]
        return {
            "items": items,
            "count": len(items),
            "worker_file_base_url": settings.worker_file_base_url or None,
        }
    finally:
        db.close()


# --- 8. Candidates (issue #17) ----------------------------------------------

_EXCERPT_MAX_LINES = 12
_EXCERPT_MAX_CHARS = 1200


def _transcript_excerpt(segments: Any, start: float, end: float) -> List[Dict[str, Any]]:
    """Transcript lines overlapping [start, end] (preview of the clip)."""
    out: List[Dict[str, Any]] = []
    if not isinstance(segments, list):
        return out
    chars = 0
    for s in segments:
        if not isinstance(s, dict):
            continue
        s0, s1 = _num(s.get("start")), _num(s.get("end"))
        txt = str(s.get("text") or "").strip()
        if s0 is None or s1 is None or not txt:
            continue
        if s1 <= start or s0 >= end:
            continue
        out.append({"start": s0, "end": s1, "text": txt})
        chars += len(txt)
        if len(out) >= _EXCERPT_MAX_LINES or chars >= _EXCERPT_MAX_CHARS:
            break
    return out


@router.get("/candidates", response_model=CandidatesOut)
def candidates_list(
    _a: bool = Depends(_enabled_or_404),
    _b: bool = Depends(require_bearer),
    campaign_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None, max_length=32),
    limit: int = Query(200, ge=1),
):
    """Clip candidates (step 13-14) with preview metadata for manual review.

    Read-only: approve/reject live in /candidates/{id}/approve|reject
    (write token). Pending candidates first, then by score.
    """
    db = _get_db()
    try:
        params: Dict[str, Any] = {"lim": _clamp_limit(limit, default=200)}
        base = "1=1"
        if campaign_id is not None:
            base += " AND cd.campaign_id = :cid"
            params["cid"] = campaign_id
        where = base
        if status:
            where += " AND cd.status = :st"
            params["st"] = status
        rows = db.execute(
            text(
                f"""
                SELECT cd.id, cd.campaign_id, cd.asset_id, cd.start_time, cd.end_time,
                       cd.score, cd.reasoning, cd.extra_metadata, cd.status,
                       cd.created_at, cd.updated_at,
                       c.name AS campaign_name,
                       a.source_url AS asset_source_url,
                       a.extra_metadata->>'title' AS asset_title,
                       a.duration_seconds AS asset_duration,
                       a.status AS asset_status,
                       j.status AS render_job_status,
                       cl.id AS clip_id
                FROM candidates cd
                LEFT JOIN campaigns c ON c.id = cd.campaign_id
                LEFT JOIN assets a ON a.id = cd.asset_id
                LEFT JOIN jobs j ON j.id::text = cd.extra_metadata->>'render_job_id'
                LEFT JOIN LATERAL (
                    SELECT id FROM clips WHERE clips.candidate_id = cd.id
                    ORDER BY created_at DESC LIMIT 1
                ) cl ON TRUE
                WHERE {where}
                ORDER BY (cd.status = 'pending') DESC, cd.score DESC NULLS LAST,
                         cd.created_at DESC
                LIMIT :lim
                """
            ),
            params,
        ).all()
        counts = {
            r.status: int(r.n)
            for r in db.execute(
                text(f"SELECT cd.status, count(*) AS n FROM candidates cd WHERE {base} GROUP BY 1"),
                params,
            ).all()
        }
        asset_ids = list({r.asset_id for r in rows})
        segments: Dict[Any, Any] = {}
        if asset_ids:
            for a in db.execute(
                text(
                    "SELECT id, extra_metadata->'transcription'->'segments' AS segs "
                    "FROM assets WHERE id = ANY(:ids)"
                ),
                {"ids": asset_ids},
            ).all():
                segments[a.id] = a.segs
        items = []
        for r in rows:
            meta = r.extra_metadata or {}
            start, end = float(r.start_time), float(r.end_time)
            items.append(
                {
                    "id": str(r.id),
                    "campaign_id": r.campaign_id,
                    "campaign_name": r.campaign_name,
                    "asset_id": str(r.asset_id),
                    "asset_source_url": r.asset_source_url,
                    "asset_title": r.asset_title,
                    "asset_duration_seconds": r.asset_duration,
                    "asset_status": r.asset_status,
                    "start_time": start,
                    "end_time": end,
                    "duration_seconds": round(end - start, 2),
                    "score": r.score,
                    "reasoning": r.reasoning,
                    "title": meta.get("title") or None,
                    "caption": meta.get("caption") or None,
                    "source": meta.get("source"),
                    "kind": meta.get("kind"),
                    "status": r.status,
                    "approved_at": meta.get("approved_at"),
                    "rejected_at": meta.get("rejected_at"),
                    "rejected_reason": meta.get("rejected_reason"),
                    "render_job_id": meta.get("render_job_id"),
                    "render_job_status": r.render_job_status,
                    "clip_id": str(r.clip_id) if r.clip_id else None,
                    "transcript_excerpt": _transcript_excerpt(
                        segments.get(r.asset_id), start, end
                    ),
                    "created_at": _iso(r.created_at),
                    "updated_at": _iso(r.updated_at),
                }
            )
        return {"items": items, "count": len(items), "counts_by_status": counts}
    finally:
        db.close()
