"""Job service: state transitions, atomic claim, retry logic, lease recovery."""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.logctx import format_ids, ids_from_job, redact
from app.models.job import JOB_STATUS_VALUES, Job, JobStatus
from app.services.download_errors import resolve_failure


LEASE_DURATION_SECONDS = 300  # 5 minutes

# payload key pinning a job to the worker that holds its local files.
AFFINITY_KEY = "affinity_worker_id"
# Job types that need no local file from a previous step.
SELF_CONTAINED_JOB_TYPES = ("download",)


def _fallback_ids() -> set[str]:
    from app.config import settings
    return {w.strip() for w in (settings.fallback_worker_ids or "").split(",") if w.strip()}


def _fresh_worker_ids(db: Session, now: datetime) -> set[str]:
    from app.config import settings
    from app.models.worker import Worker, WorkerStatus
    cutoff = now - timedelta(seconds=settings.worker_stale_seconds)
    rows = db.execute(
        select(Worker.worker_id).where(
            Worker.last_heartbeat_at.is_not(None),
            Worker.last_heartbeat_at >= cutoff,
            Worker.status != WorkerStatus.DISABLED.value,
        )
    ).scalars()
    return set(rows)


def _claim_filter(db: Session, worker_id: str, now: datetime):
    """Which pending jobs `worker_id` may take.

    - Pinned to me (payload affinity_worker_id): always.
    - Primary worker: also unpinned jobs, and self-contained (download) jobs
      pinned to a stale worker.
    - Fallback worker (FALLBACK_WORKER_IDS): only while no primary worker is
      fresh, and only self-contained jobs that are unpinned or pinned to a
      stale worker. Everything downstream of its own downloads is pinned to it.
    Non-download jobs pinned to a stale worker wait for it: their input files
    live on that worker's disk.
    """
    fallback = _fallback_ids()
    fresh = _fresh_worker_ids(db, now) | {worker_id}
    aff = Job.payload[AFFINITY_KEY].astext
    mine = aff == worker_id
    free_download = Job.job_type.in_(SELF_CONTAINED_JOB_TYPES) & or_(
        aff.is_(None), aff.not_in(sorted(fresh))
    )
    if worker_id in fallback:
        if fresh - fallback:
            return mine
        return or_(mine, free_download)
    return or_(aff.is_(None), mine, free_download)


VALID_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.PENDING: {JobStatus.ASSIGNED, JobStatus.CANCELLED},
    JobStatus.ASSIGNED: {JobStatus.PROCESSING, JobStatus.PENDING, JobStatus.CANCELLED},
    JobStatus.PROCESSING: {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.PENDING},
    JobStatus.FAILED: {JobStatus.PENDING},
    JobStatus.COMPLETED: set(),
    JobStatus.CANCELLED: set(),
}


class InvalidTransition(Exception):
    pass


logger = logging.getLogger(__name__)


class JobNotFound(Exception):
    pass


class WorkerMismatch(Exception):
    pass


def _backoff_seconds(attempts: int) -> int:
    return 30 * (2 ** max(0, attempts - 1))


def _ensure_transition(current: JobStatus, new: JobStatus) -> None:
    if new not in VALID_TRANSITIONS.get(current, set()):
        raise InvalidTransition(f"invalid transition {current.value} -> {new.value}")


def create_job(
    db: Session,
    *,
    job_type: str,
    payload: dict,
    priority: int = 5,
    max_attempts: int = 3,
    available_at: datetime | None = None,
    affinity_worker_id: str | None = None,
) -> Job:
    """Create a pending job. `available_at` schedules it: `claim_next_job` only
    hands out jobs whose ``available_at <= now``, so future datetimes hold the
    job back (publish scheduling #MOLI-53). Default: available immediately."""
    if affinity_worker_id:
        # Jobs that read files from a worker's own disk must run there.
        payload = {**payload, AFFINITY_KEY: affinity_worker_id}
    job = Job(
        job_type=job_type,
        payload=payload,
        priority=priority,
        max_attempts=max_attempts,
        status=JobStatus.PENDING.value,
        available_at=available_at or datetime.now(timezone.utc),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def get_job(db: Session, job_id: UUID) -> Optional[Job]:
    return db.get(Job, job_id)


def list_jobs(
    db: Session,
    *,
    status: Optional[JobStatus] = None,
    job_type: Optional[str] = None,
    worker_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Job]:
    q = select(Job)
    if status is not None:
        q = q.where(Job.status == status.value)
    if job_type is not None:
        q = q.where(Job.job_type == job_type)
    if worker_id is not None:
        q = q.where(Job.worker_id == worker_id)
    q = q.order_by(Job.created_at.desc()).limit(limit).offset(offset)
    return list(db.execute(q).scalars().all())


def _recover_expired_leases(db: Session, now: datetime) -> int:
    stmt = (
        update(Job)
        .where(
            Job.status.in_([JobStatus.ASSIGNED.value, JobStatus.PROCESSING.value]),
            Job.lease_until.is_not(None),
            Job.lease_until < now,
        )
        .values(
            status=JobStatus.PENDING.value,
            lease_until=None,
            worker_id=None,
        )
        .returning(Job.id, Job.job_type, Job.payload)
    )
    rows = db.execute(stmt).all()
    for row in rows:
        payload = row.payload if isinstance(row.payload, dict) else {}
        logger.warning(
            "lease expired, requeue %s",
            format_ids(
                job_id=row.id,
                job_type=row.job_type,
                clip_id=payload.get("clip_id"),
                campaign_id=payload.get("campaign_id"),
                asset_id=payload.get("asset_id"),
            ),
        )
    return len(rows)


def claim_next_job(db: Session, *, worker_id: str) -> Optional[Job]:
    now = datetime.now(timezone.utc)
    lease_expires = now + timedelta(seconds=LEASE_DURATION_SECONDS)
    _recover_expired_leases(db, now)
    claim_q = (
        select(Job)
        .where(
            Job.status == JobStatus.PENDING.value,
            Job.available_at <= now,
            _claim_filter(db, worker_id, now),
        )
        .order_by(Job.priority.desc(), Job.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job = db.execute(claim_q).scalar_one_or_none()
    if job is None:
        db.commit()
        return None
    job.status = JobStatus.ASSIGNED.value
    job.worker_id = worker_id
    job.started_at = now
    job.lease_until = lease_expires
    db.commit()
    db.refresh(job)
    return job


def start_processing(db: Session, job_id: UUID, *, worker_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise JobNotFound()
    _ensure_transition(JobStatus(job.status), JobStatus.PROCESSING)
    if job.worker_id != worker_id:
        raise WorkerMismatch()
    now = datetime.now(timezone.utc)
    job.status = JobStatus.PROCESSING.value
    job.lease_until = now + timedelta(seconds=LEASE_DURATION_SECONDS)
    db.commit()
    db.refresh(job)
    return job


def complete_job(db: Session, job_id: UUID, *, worker_id: str, result: dict) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise JobNotFound()
    _ensure_transition(JobStatus(job.status), JobStatus.COMPLETED)
    if job.worker_id != worker_id:
        raise WorkerMismatch()
    now = datetime.now(timezone.utc)
    job.status = JobStatus.COMPLETED.value
    job.result = result
    job.completed_at = now
    job.lease_until = None
    db.commit()
    db.refresh(job)
    try:
        from app.services.job_state_transitions import (
            on_download_completed,
            on_qa_completed,
            on_render_completed,
            on_transcribe_completed,
        )
        from app.services.publish_hooks import on_publish_completed
        handlers = {
            "download": on_download_completed,
            "transcribe": on_transcribe_completed,
            "render": on_render_completed,
            "qa": on_qa_completed,
            "publish": on_publish_completed,
        }
        handler = handlers.get(job.job_type)
        if handler:
            handler(db, job, result or {})
    except Exception as e:  # noqa: BLE001
        # The worker already delivered a result. Keep the job completed, but
        # do not hide the handler failure: it is why a transcribe or publish
        # looks done while the asset never moved.
        note = redact(f"{type(e).__name__}: {e}", limit=500)
        logger.exception("state transition failed %s", format_ids(**ids_from_job(job)))
        db.rollback()
        fresh = db.get(Job, job_id)
        if fresh is None:
            return job
        merged = dict(fresh.result or {})
        merged["handler_error"] = note
        fresh.result = merged
        try:
            db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "could not store handler_error %s", format_ids(**ids_from_job(job)),
            )
        return fresh
    return job


def fail_job(
    db: Session,
    job_id: UUID,
    *,
    worker_id: str,
    error_message: str,
    permanent: bool = False,
) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise JobNotFound()
    _ensure_transition(JobStatus(job.status), JobStatus.FAILED)
    if job.worker_id != worker_id:
        raise WorkerMismatch()
    terminal, message = resolve_failure(
        job.job_type, error_message, permanent=permanent
    )
    now = datetime.now(timezone.utc)
    job.attempts = job.attempts + 1
    # A private/unavailable download will not succeed on the next attempt.
    # Failing it now stops the lease from requeueing the same URL.
    if terminal or job.attempts >= job.max_attempts:
        job.status = JobStatus.FAILED.value
        job.error_message = message
        job.lease_until = None
    else:
        job.status = JobStatus.PENDING.value
        job.error_message = message
        job.available_at = now + timedelta(seconds=_backoff_seconds(job.attempts))
        job.worker_id = None
        job.lease_until = None
    db.commit()
    db.refresh(job)
    logger.warning(
        "job fail recorded %s status=%s attempts=%s/%s error=%s",
        format_ids(**ids_from_job(job)),
        job.status,
        job.attempts,
        job.max_attempts,
        redact(message or ""),
    )
    if job.status == JobStatus.FAILED.value:
        try:
            from app.services.job_state_transitions import on_job_failed
            on_job_failed(db, job)
        except Exception as e:  # noqa: BLE001
            logger.exception(
                "on_job_failed handler failed %s: %s",
                format_ids(**ids_from_job(job)),
                redact(f"{type(e).__name__}: {e}"),
            )
    return job


def heartbeat(db: Session, job_id: UUID, *, worker_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise JobNotFound()
    if job.worker_id != worker_id:
        raise WorkerMismatch()
    current_status = JobStatus(job.status)
    if current_status not in {JobStatus.ASSIGNED, JobStatus.PROCESSING}:
        raise InvalidTransition(f"cannot heartbeat job in status {job.status}")
    now = datetime.now(timezone.utc)
    job.lease_until = now + timedelta(seconds=LEASE_DURATION_SECONDS)
    db.commit()
    db.refresh(job)
    return job


def cancel_job(db: Session, job_id: UUID) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise JobNotFound()
    _ensure_transition(JobStatus(job.status), JobStatus.CANCELLED)
    job.status = JobStatus.CANCELLED.value
    job.lease_until = None
    job.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(job)
    return job
