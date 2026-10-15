"""Structured job logs, secret redaction, and visible pipeline failures. No database."""
from __future__ import annotations

import asyncio
import io
import logging
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.logctx import RedactFilter, format_ids, redact
from app.main import app, unhandled_error
from app.services.job_state_transitions import on_job_failed
from app.services.publish_hooks import on_publish_completed

pytestmark = pytest.mark.no_db


def test_format_ids_keeps_job_clip_and_campaign():
    text = format_ids(job_id="j1", clip_id="c1", campaign_id=9, asset_id=None)
    assert text == "job_id=j1 clip_id=c1 campaign_id=9"


def test_redact_filter_hides_bearer_token():
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.addFilter(RedactFilter())
    log = logging.getLogger("test-job-logs-redact")
    log.handlers = [handler]
    log.propagate = False
    log.setLevel(logging.INFO)
    try:
        log.info("calling grok with Bearer supersecretvalue")
    finally:
        log.handlers = []
    assert "supersecretvalue" not in buf.getvalue()
    assert "[redacted]" in buf.getvalue()


def test_redact_drops_api_key():
    assert "xai-secretvalue" not in redact("key xai-secretvalue")


def test_unhandled_error_does_not_echo_the_token():
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "path": "/jobs",
        "raw_path": b"/jobs",
        "query_string": b"",
        "headers": [],
        "scheme": "http",
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }
    from starlette.requests import Request

    response = asyncio.run(unhandled_error(Request(scope), RuntimeError("Bearer supersecretvalue")))
    body = response.body.decode()
    assert response.status_code == 500
    assert body == '{"detail":"internal error"}'
    assert "supersecretvalue" not in body


def test_auth_error_stays_a_client_error():
    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/system/info")
    assert response.status_code in {401, 403}
    assert response.json().get("detail") != "internal error"


class _DB:
    def __init__(self, row):
        self.row = row
        self.commits = 0

    def get(self, model, row_id):
        return self.row

    def commit(self):
        self.commits += 1


def test_publish_unexpected_status_is_failed_not_left_pending():
    pub = SimpleNamespace(
        id=uuid.uuid4(),
        status="pending",
        error_message=None,
        post_url=None,
        posted_at=None,
    )
    job = SimpleNamespace(
        id=uuid.uuid4(),
        job_type="publish",
        error_message=None,
        payload={
            "publication_id": str(pub.id),
            "clip_id": str(uuid.uuid4()),
            "campaign_id": 4,
            "dry_run": False,
        },
    )
    db = _DB(pub)
    on_publish_completed(db, job, {"dry_run": False, "publications": [{"status": "queued"}]})
    assert pub.status == "failed"
    assert pub.error_message == "unexpected publish status: queued"
    assert db.commits == 1


def test_publish_posted_still_records_the_url():
    pub = SimpleNamespace(
        id=uuid.uuid4(),
        status="pending",
        error_message="old",
        post_url=None,
        posted_at=None,
    )
    job = SimpleNamespace(
        id=uuid.uuid4(),
        job_type="publish",
        error_message=None,
        payload={"publication_id": str(pub.id), "clip_id": str(uuid.uuid4()), "campaign_id": 4},
    )
    on_publish_completed(_DB(pub), job, {"publications": [{"status": "posted", "post_url": "https://youtu.be/abc"}]})
    assert pub.status == "posted"
    assert pub.post_url == "https://youtu.be/abc"
    assert pub.error_message is None


def test_transcribe_failure_keeps_a_reason_without_the_token():
    asset = SimpleNamespace(status="downloaded", extra_metadata={})
    job = SimpleNamespace(
        id=uuid.uuid4(),
        job_type="transcribe",
        payload={"asset_id": str(uuid.uuid4()), "campaign_id": 8},
        error_message="whisper crashed Bearer supersecretvalue",
        result={},
    )
    on_job_failed(_DB(asset), job)
    assert asset.status == "failed"
    assert asset.extra_metadata["last_error_kind"] == "transcribe_failed"
    assert "supersecretvalue" not in asset.extra_metadata["last_error_detail"]
    assert "whisper crashed" in asset.extra_metadata["last_error_detail"]
    assert "skip_download" not in asset.extra_metadata
