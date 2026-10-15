"""Error text that is safe to return from the API or print from a cron."""
from __future__ import annotations

import re

_SECRET = re.compile(
    r"(?i)"
    r"(bearer\s+)\S+"
    r"|((?:api[_-]?key|access[_-]?token|token|password|secret|authorization)\s*[=:]\s*)\S+"
    r"|(xai-[A-Za-z0-9_\-]{6,})"
    r"|(sk-[A-Za-z0-9_\-]{6,})"
)


def redact(text: str, *, limit: int = 300) -> str:
    if not text:
        return ""

    def _sub(match: re.Match[str]) -> str:
        if match.group(1):
            return match.group(1) + "[redacted]"
        if match.group(2):
            return match.group(2) + "[redacted]"
        return "[redacted]"

    return _SECRET.sub(_sub, str(text))[:limit]


def public_error(exc: BaseException, *, limit: int = 180) -> str:
    """Type plus a short redacted detail. No tokens, no stack."""
    detail = redact(str(exc), limit=limit)
    name = type(exc).__name__
    if not detail:
        return name
    text = f"{name}: {detail}"
    return text[:limit]
