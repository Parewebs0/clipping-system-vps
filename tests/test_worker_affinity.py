"""Worker affinity + CPU fallback on claim_next_job."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.config import settings
from app.models.worker import Worker
from app.services import job_service

GPU, CPU = "win-gpu", "minipc-cpu"


@pytest.fixture
def fleet(db, monkeypatch):
    monkeypatch.setattr(settings, "fallback_worker_ids", CPU)
    monkeypatch.setattr(settings, "worker_stale_seconds", 600)
    db.execute(text("DELETE FROM workers"))
    db.commit()

    def beat(worker_id, minutes_ago):
        w = db.get(Worker, worker_id) or Worker(worker_id=worker_id, status="online")
        w.last_heartbeat_at = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
        db.merge(w)
        db.commit()

    yield beat
    db.execute(text("DELETE FROM workers"))
    db.commit()


def _claim(db, wid):
    return job_service.claim_next_job(db, worker_id=wid)


def test_fallback_idle_while_gpu_fresh(db, fleet):
    fleet(GPU, 1)
    fleet(CPU, 0)
    job_service.create_job(db, job_type="download", payload={"a": 1})
    assert _claim(db, CPU) is None
    assert _claim(db, GPU) is not None


def test_fallback_takes_download_when_gpu_stale(db, fleet):
    fleet(GPU, 30)
    fleet(CPU, 0)
    job_service.create_job(db, job_type="download", payload={"a": 1})
    job = _claim(db, CPU)
    assert job is not None and job.worker_id == CPU


def test_fallback_takes_download_when_gpu_never_registered(db, fleet):
    fleet(CPU, 0)
    job_service.create_job(db, job_type="download", payload={"a": 1})
    assert _claim(db, CPU) is not None


def test_fallback_never_takes_unpinned_non_download(db, fleet):
    fleet(GPU, 30)
    job_service.create_job(db, job_type="render", payload={"input_video": "C:\\x.mp4"})
    assert _claim(db, CPU) is None


def test_pinned_job_only_goes_to_its_worker(db, fleet):
    fleet(GPU, 1)
    fleet(CPU, 0)
    job_service.create_job(db, job_type="transcribe", payload={}, affinity_worker_id=CPU)
    assert _claim(db, GPU) is None
    job = _claim(db, CPU)
    assert job is not None and job.payload["affinity_worker_id"] == CPU


def test_pinned_render_waits_for_stale_owner(db, fleet):
    fleet(GPU, 30)
    job_service.create_job(db, job_type="render", payload={}, affinity_worker_id=GPU)
    assert _claim(db, CPU) is None


def test_pinned_download_released_when_owner_stale(db, fleet):
    fleet(GPU, 30)
    job_service.create_job(db, job_type="download", payload={}, affinity_worker_id=GPU)
    assert _claim(db, CPU) is not None


def test_primary_unaffected_without_fallback_config(db, monkeypatch):
    monkeypatch.setattr(settings, "fallback_worker_ids", "")
    job_service.create_job(db, job_type="render", payload={})
    assert _claim(db, "any-worker") is not None


def test_polling_counts_as_liveness(db, fleet):
    """Workers don't call /worker/heartbeat; a poll must keep the GPU fresh."""
    assert _claim(db, GPU) is None  # empty queue, but registers liveness
    job_service.create_job(db, job_type="download", payload={"a": 1})
    assert _claim(db, CPU) is None
    db.expire_all()
    assert db.get(Worker, GPU).last_heartbeat_at is not None


def test_job_lease_heartbeat_counts_as_liveness(db, fleet):
    fleet(GPU, 30)
    job_service.create_job(db, job_type="render", payload={})
    job = _claim(db, GPU)
    fleet(GPU, 30)
    job_service.heartbeat(db, job.id, worker_id=GPU)
    job_service.create_job(db, job_type="download", payload={"a": 1})
    assert _claim(db, CPU) is None
