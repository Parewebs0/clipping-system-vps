"""Structured job ids and secret redaction for pipeline logs."""
from __future__ import annotations

import logging
import re
import traceback

_SECRET = re.compile(
    r"(?i)"
    r"(bearer\s+)\S+"
    r"|((?:api[_-]?key|access[_-]?token|token|password|secret|authorization)\s*[=:]\s*)\S+"
    r"|(xai-[A-Za-z0-9_\-]{6,})"
    r"|(sk-[A-Za-z0-9_\-]{6,})"
)


def redact(text: str, *, limit: int = 300) -> str:
    """Drop credentials from a message and cap its length."""
    if not text:
        return ""

    def _sub(match: re.Match[str]) -> str:
        if match.group(1):
            return match.group(1) + "[redacted]"
        if match.group(2):
            return match.group(2) + "[redacted]"
        return "[redacted]"

    return _SECRET.sub(_sub, str(text))[:limit]


def format_ids(
    *,
    job_id=None,
    job_type=None,
    clip_id=None,
    campaign_id=None,
    asset_id=None,
) -> str:
    """One stable field list: job_id, clip_id, campaign_id when we have them."""
    parts: list[str] = []
    for key, value in (
        ("job_id", job_id),
        ("job_type", job_type),
        ("clip_id", clip_id),
        ("campaign_id", campaign_id),
        ("asset_id", asset_id),
    ):
        if value is not None and str(value) != "":
            parts.append(f"{key}={value}")
    return " ".join(parts) or "job_id=unknown"


def ids_from_job(job) -> dict:
    payload = job.payload if isinstance(getattr(job, "payload", None), dict) else {}
    return {
        "job_id": getattr(job, "id", None),
        "job_type": getattr(job, "job_type", None),
        "clip_id": payload.get("clip_id"),
        "campaign_id": payload.get("campaign_id"),
        "asset_id": payload.get("asset_id"),
    }


class RedactFilter(logging.Filter):
    """Rewrite log records so tokens never reach a handler."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        record.msg = redact(message, limit=2000)
        record.args = ()
        if record.exc_info and record.exc_info[0] is not None:
            text = "".join(traceback.format_exception(*record.exc_info))
            record.exc_text = redact(text, limit=4000)
        return True


def install_redact_filter() -> None:
    root = logging.getLogger()
    for handler in root.handlers:
        if not any(isinstance(existing, RedactFilter) for existing in handler.filters):
            handler.addFilter(RedactFilter())
