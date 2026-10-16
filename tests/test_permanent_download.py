"""Private/unavailable downloads: /fail accepts the worker body and does not retry.

No database. The HTTP check overrides auth and the session so a long ANSI
traceback is validated the same way the worker sends it.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.jobs import WorkerFailCreate
from app.auth import require_bearer
from app.db.database import get_db
from app.main import app
from app.services.download_errors import resolve_failure
from app.services.job_service import fail_job
from app.services.job_state_transitions import on_job_failed

pytestmark = pytest.mark.no_db

PRIVATE = (
    "ERROR: [youtube] 7rX5cPQrx_s: Private video. "
    "Sign in if you've been granted access to this video"
)


def _traceback(reason: str) -> str:
    frames = "  File \"job_runner.py\", line 241, in _handle_failure\n" * 80
    return (
        "\x1b[0;31mTraceback (most recent call last):\n"
        + frames
        + f"yt_dlp.utils.DownloadError: {reason}\x1b[0m"
    )


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return list(self.rows)


class _DB:
    def __init__(self, job, asset=None, campaign=None, assets=None, jobs=None):
        self.job = job
        self.asset = asset
        self.campaign = campaign
        self.assets = list(assets if assets is not None else ([asset] if asset else []))
        self.jobs = list(jobs or [])
        self.commits = 0

    def get(self, model, row_id):
        name = getattr(model, "__name__", "")
        if name == "Job":
            return self.job
        if name == "Campaign":
            return self.campaign
        return self.asset

    def query(self, model):
        name = getattr(model, "__name__", "")
        if name == "Asset":
            return _Query(self.assets)
        return _Query(self.jobs)

    def commit(self):
        self.commits += 1

    def refresh(self, obj):
        return None


def _job(**overrides):
    data = dict(
        id=uuid.uuid4(),
        status="processing",
        worker_id="windows-gpu-worker-01",
        attempts=0,
        max_attempts=3,
        job_type="download",
        error_message=None,
        available_at=None,
        lease_until=datetime.now(timezone.utc),
        payload={},
        result=None,
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def _asset(campaign_id=18, **overrides):
    data = dict(
        id=uuid.uuid4(),
        campaign_id=campaign_id,
        status="pending",
        asset_type="video",
        source_url="https://youtu.be/7rX5cPQrx_s",
        extra_metadata={},
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def _campaign(campaign_id=18, **overrides):
    data = dict(
        id=campaign_id,
        status="scored",
        source_metadata={},
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def test_worker_body_is_not_rejected_for_length_or_ansi():
    raw = _traceback(PRIVATE)
    assert len(raw) > 2048
    payload = WorkerFailCreate.model_validate(
        {
            "error_message": raw,
            "permanent": True,
            "stage": "download",
            "stack_trace": raw,
        }
    )
    assert payload.permanent is True
    assert "\x1b" not in payload.error_message
    assert "Private video" in payload.error_message
    assert len(payload.error_message) <= 65536


def test_alias_error_field_fills_error_message():
    payload = WorkerFailCreate.model_validate({"error": "Private video"})
    assert "Private video" in payload.error_message
    assert payload.permanent is False


def test_ansi_only_message_does_not_fail_validation():
    payload = WorkerFailCreate.model_validate({"error_message": "\x1b[0m"})
    assert payload.error_message == "unknown error"


def test_fail_endpoint_accepts_the_worker_traceback(monkeypatch):
    captured = {}

    def fake_fail(db, job_id, *, worker_id, error_message, permanent=False):
        captured["message"] = error_message
        captured["permanent"] = permanent
        captured["worker_id"] = worker_id
        now = datetime.now(timezone.utc)
        return SimpleNamespace(
            id=job_id,
            job_type="download",
            status="failed",
            priority=5,
            payload={},
            result=None,
            error_message=error_message[:2048],
            worker_id=worker_id,
            attempts=1,
            max_attempts=3,
            created_at=now,
            updated_at=now,
            available_at=now,
            started_at=now,
            completed_at=None,
            lease_until=None,
        )

    monkeypatch.setattr("app.services.job_service.fail_job", fake_fail)

    def _session():
        yield SimpleNamespace()

    app.dependency_overrides[get_db] = _session
    app.dependency_overrides[require_bearer] = lambda: True
    try:
        raw = _traceback(PRIVATE)
        response = TestClient(app).post(
            f"/worker/jobs/{uuid.uuid4()}/fail?worker_id=windows-gpu-worker-01",
            json={"error_message": raw, "stage": "download", "permanent": True},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    assert captured["worker_id"] == "windows-gpu-worker-01"
    assert captured["permanent"] is True
    assert "\x1b" not in captured["message"]
    assert "Private video" in captured["message"]


def test_private_video_is_terminal_and_keeps_the_reason():
    raw = _traceback(PRIVATE)
    terminal, stored = resolve_failure("download", raw, permanent=False)
    assert terminal is True
    assert stored.startswith("video_private:")
    assert "Private video" in stored
    assert "\x1b" not in stored
    assert len(stored) <= 2048


def test_transient_download_error_still_retries():
    terminal, stored = resolve_failure(
        "download",
        "ERROR: unable to download video data: HTTP Error 503",
        permanent=False,
    )
    assert terminal is False
    assert "503" in stored
    assert "\x1b" not in stored


def test_fail_job_does_not_requeue_a_private_video():
    asset = _asset()
    campaign = _campaign()
    job = _job(payload={"asset_id": str(asset.id), "campaign_id": "18"})
    db = _DB(job, asset, campaign)
    failed = fail_job(
        db,
        job.id,
        worker_id="windows-gpu-worker-01",
        error_message=_traceback(PRIVATE),
    )
    assert failed.status == "failed"
    assert failed.attempts == 1
    assert failed.lease_until is None
    assert "\x1b" not in failed.error_message
    assert failed.error_message.startswith("video_private:")
    assert asset.status == "failed"
    assert asset.extra_metadata["skip_download"] is True
    assert asset.extra_metadata["unavailable"] is True
    assert asset.extra_metadata["last_error_kind"] == "video_private"
    assert campaign.status == "blocked_no_assets"
    assert campaign.source_metadata["no_assets"]["reason"] == "permanent_download_failure"
    assert any(
        entry["to"] == "blocked_no_assets" and str(entry["reason"]).startswith("no_assets:")
        for entry in campaign.source_metadata["status_history"]
    )


def test_fail_job_retries_a_transient_download_error():
    asset = _asset()
    job = _job(payload={"asset_id": str(asset.id), "campaign_id": "18"})
    db = _DB(job, asset, _campaign())
    failed = fail_job(
        db,
        job.id,
        worker_id="windows-gpu-worker-01",
        error_message="\x1b[31mERROR: HTTP Error 503\x1b[0m",
    )
    assert failed.status == "pending"
    assert failed.attempts == 1
    assert "\x1b" not in failed.error_message
    assert asset.status == "pending"


def test_downloaded_sibling_keeps_the_campaign_scored():
    failed_asset = _asset()
    sibling = _asset(status="downloaded", source_url="https://youtu.be/okvideoid")
    campaign = _campaign()
    job = _job(payload={"asset_id": str(failed_asset.id), "campaign_id": "18"})
    db = _DB(job, failed_asset, campaign, assets=[failed_asset, sibling])
    fail_job(
        db,
        job.id,
        worker_id="windows-gpu-worker-01",
        error_message="Video unavailable",
    )
    assert failed_asset.status == "failed"
    assert failed_asset.extra_metadata["skip_download"] is True
    assert sibling.status == "downloaded"
    assert campaign.status == "scored"


def test_pending_sibling_keeps_the_campaign_scored():
    failed_asset = _asset()
    sibling = _asset(source_url="https://www.youtube.com/watch?v=abcdefghijk")
    campaign = _campaign()
    job = _job(payload={"asset_id": str(failed_asset.id), "campaign_id": "18"})
    db = _DB(job, failed_asset, campaign, assets=[failed_asset, sibling])
    fail_job(
        db,
        job.id,
        worker_id="windows-gpu-worker-01",
        error_message="Join this channel to get access to members-only content",
    )
    assert campaign.status == "scored"
    assert sibling.status == "pending"


def test_inflight_download_keeps_the_campaign_scored():
    asset = _asset()
    campaign = _campaign()
    job = _job(payload={"asset_id": str(asset.id), "campaign_id": "18"})
    other = SimpleNamespace(
        job_type="download",
        status="processing",
        payload={"campaign_id": "18", "asset_id": "other"},
    )
    db = _DB(job, asset, campaign, jobs=[other])
    fail_job(
        db,
        job.id,
        worker_id="windows-gpu-worker-01",
        error_message="This video has been removed by the uploader",
    )
    assert asset.extra_metadata["last_error_kind"] == "video_removed"
    assert campaign.status == "scored"


def test_permanent_flag_without_a_known_phrase_is_terminal_for_downloads():
    asset = _asset()
    campaign = _campaign()
    job = _job(payload={"asset_id": str(asset.id), "campaign_id": "18"})
    db = _DB(job, asset, campaign)
    failed = fail_job(
        db,
        job.id,
        worker_id="windows-gpu-worker-01",
        error_message="yt-dlp gave up",
        permanent=True,
    )
    assert failed.status == "failed"
    assert failed.error_message.startswith("unavailable:")
    assert asset.extra_metadata["unavailable"] is True
    assert campaign.status == "blocked_no_assets"


def test_permanent_flag_does_not_skip_retries_for_other_job_types():
    job = _job(job_type="transcribe", payload={})
    db = _DB(job)
    failed = fail_job(
        db,
        job.id,
        worker_id="windows-gpu-worker-01",
        error_message="Private video",
        permanent=True,
    )
    assert failed.status == "pending"
    assert failed.attempts == 1


def test_age_and_login_markers_are_permanent():
    assert resolve_failure("download", "Sign in to confirm your age", permanent=False)[0]
    assert resolve_failure("download", "ERROR: login required", permanent=False)[0]


def test_on_job_failed_still_explains_a_transcribe_error():
    asset = SimpleNamespace(status="downloaded", extra_metadata={}, campaign_id=8)
    job = SimpleNamespace(
        id=uuid.uuid4(),
        job_type="transcribe",
        payload={"asset_id": str(uuid.uuid4()), "campaign_id": 8},
        error_message="whisper crashed Bearer supersecretvalue",
        result={},
    )
    on_job_failed(_DB(job, asset), job)
    assert asset.status == "failed"
    assert asset.extra_metadata["last_error_kind"] == "transcribe_failed"
    assert "skip_download" not in asset.extra_metadata
