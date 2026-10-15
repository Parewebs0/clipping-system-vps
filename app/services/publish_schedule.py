"""Publish scheduling (#MOLI-53): per-platform time slots → scheduled_at (UTC).

Slots come from the environment, one variable per platform:

    PUBLISH_SLOT_TIKTOK=18:00-22:00
    PUBLISH_SLOT_INSTAGRAM=12:00-14:00,18:00-22:00
    PUBLISH_SLOT_YOUTUBE=           # empty/absent → publish as soon as approved

Windows are UTC. A platform with no configured slot keeps the current
behaviour: the publish job is available immediately (available_at = now).
Outside a window the job is scheduled at the START of the next window, so a
scheduled job never goes out before its hour (`claim_next_job` filters
``available_at <= now`` and the worker re-checks the payload).
"""
from __future__ import annotations

import os
from datetime import datetime, time, timedelta, timezone

ENV_PREFIX = "PUBLISH_SLOT_"


def slot_env_name(platform: str) -> str:
    return ENV_PREFIX + str(platform or "").strip().upper()


def parse_slots(spec: str | None) -> list[tuple[time, time]]:
    """Parse ``"18:00-22:00,08:00-10:00"`` → [(8:00,10:00), (18:00,22:00)].

    Entries that are empty, malformed or inverted are dropped (an inverted
    window would never be satisfiable). Return [] when spec is empty.
    """
    out: list[tuple[time, time]] = []
    for chunk in (spec or "").split(","):
        chunk = chunk.strip()
        if not chunk or "-" not in chunk:
            continue
        a, b = (part.strip() for part in chunk.split("-", 1))
        try:
            t0 = _parse_hhmm(a)
            t1 = _parse_hhmm(b)
        except (TypeError, ValueError):
            continue
        if t0 is None or t1 is None or t0 >= t1:
            continue
        out.append((t0, t1))
    return out


def _parse_hhmm(raw: str) -> time | None:
    parts = raw.split(":")
    if len(parts) != 2 or len(parts[0]) != 2 or len(parts[1]) != 2:
        return None
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour=hour, minute=minute)


def env_slots(platform: str) -> list[tuple[time, time]]:
    return parse_slots(os.environ.get(slot_env_name(platform), ""))


def next_scheduled(platform: str, now: datetime, slots: list[tuple[time, time]] | None = None) -> datetime:
    """The UTC datetime the job becomes available: now inside a slot, else the
    next slot start (tomorrow when today's start already passed). Now must be
    timezone-aware; no slot configured → now (unchanged behaviour)."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if slots is None:
        slots = env_slots(platform)
    if not slots:
        return now
    t = now.astimezone(timezone.utc)
    today = t.date()
    for start_t, end_t in sorted(slots):
        start = datetime.combine(today, start_t, tzinfo=timezone.utc)
        end = datetime.combine(today, end_t, tzinfo=timezone.utc)
        if start <= t < end:
            return t
        if t < start:
            return start
    start = datetime.combine(today + timedelta(days=1), min(sorted(slots))[0], tzinfo=timezone.utc)
    return start


def scheduled_at_for(platform: str, now: datetime | None = None) -> datetime:
    """scheduled_at (aware UTC) for one publish. Also used by tests."""
    if now is None:
        now = datetime.now(timezone.utc)
    return next_scheduled(platform, now)