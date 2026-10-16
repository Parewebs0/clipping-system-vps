"""Permanent yt-dlp download failures and /fail message cleanup.

The Windows worker posts the same phrases (see its download_errors module).
A private, unavailable, removed, members-only or age/login video will never
download, so the job must not be retried and the asset must not be enqueued
again.
"""
from __future__ import annotations

import re

# CSI color codes yt-dlp prints, e.g. ESC[0;31m ... ESC[0m.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# Accepted on the wire. The row stores a shorter copy (see STORED_LIMIT).
INCOMING_LIMIT = 65536
STORED_LIMIT = 2048

# More specific needles first. Matched case-insensitively against the
# yt-dlp error text (often the last line of a traceback).
_MARKERS: tuple[tuple[str, str], ...] = (
    ("private video", "video_private"),
    ("video is private", "video_private"),
    ("members-only", "members_only"),
    ("members only", "members_only"),
    ("join this channel to get access", "members_only"),
    ("sign in to confirm your age", "age_restricted"),
    ("confirm your age", "age_restricted"),
    ("age-restricted", "age_restricted"),
    ("age restricted", "age_restricted"),
    ("login required", "login_required"),
    ("sign in to view this video", "login_required"),
    ("removed by the uploader", "video_removed"),
    ("has been removed", "video_removed"),
    ("account associated with this video has been terminated", "video_unavailable"),
    ("this video is unavailable", "video_unavailable"),
    ("this video isn't available", "video_unavailable"),
    ("this video is not available", "video_unavailable"),
    ("video unavailable", "video_unavailable"),
    ("no longer available", "video_unavailable"),
    ("content isn't available", "video_unavailable"),
)

# `unavailable` is the flag the worker sends when it already decided the
# failure is permanent but the phrase was not one of the markers above.
PERMANENT_DOWNLOAD_KINDS = frozenset(kind for _, kind in _MARKERS) | {"unavailable"}


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text or "")


def permanent_download_kind(text: str) -> str | None:
    lowered = strip_ansi(text or "").lower()
    for needle, kind in _MARKERS:
        if needle in lowered:
            return kind
    return None


def clip_error_message(text: str, limit: int = STORED_LIMIT) -> str:
    """ANSI-free message of at most `limit` chars.

    A long traceback keeps its head and its tail. yt-dlp puts the reason on
    the last line, so a head-only cut used to drop "Private video" and the
    API then retried the job.
    """
    cleaned = strip_ansi(str(text or "")).replace("\x00", "").strip()
    if not cleaned:
        return "unknown error"
    if len(cleaned) <= limit:
        return cleaned
    if limit < 8:
        return cleaned[:limit]
    head = (limit - 1) // 2
    tail = limit - 1 - head
    return cleaned[:head] + "…" + cleaned[-tail:]


def resolve_failure(job_type: str, error_message: str, *, permanent: bool) -> tuple[bool, str]:
    """Return (terminal, stored_message).

    Only download jobs are terminal on the first report. Other job types keep
    their attempt budget even if the body says permanent.
    """
    full = clip_error_message(error_message, INCOMING_LIMIT)
    kind: str | None = None
    if job_type == "download":
        kind = permanent_download_kind(full)
        if permanent and kind is None:
            kind = "unavailable"
    stored = clip_error_message(full, STORED_LIMIT)
    if kind and not (stored.startswith(f"{kind}:") or stored == kind):
        stored = clip_error_message(f"{kind}: {stored}", STORED_LIMIT)
    return kind is not None, stored
