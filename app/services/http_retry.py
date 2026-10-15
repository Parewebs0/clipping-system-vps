"""Shared helpers for transient HTTP retries.

Call sites that talk to Grok, YouTube, Instagram, TikTok, or any other
public URL should retry timeouts and 429/5xx, and should never put a
token or API key into an error string. Upload to those networks still
happens on the worker; this module only covers the VPS calls.
"""
from __future__ import annotations

import re
import time

# 408/425 are rare; 429 and the usual gateway errors are the ones we see.
RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

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

    cleaned = _SECRET.sub(_sub, str(text))
    return cleaned[:limit]


def pause(attempt: int, *, backoff_s: float, sleep=None) -> None:
    """Sleep ``backoff_s * 2**(attempt-1)``. ``attempt`` is 1-based."""
    (sleep or time.sleep)(backoff_s * (2 ** max(0, attempt - 1)))
