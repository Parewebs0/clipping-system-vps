"""publish_schedule unit tests — pure, no DB (MOLI-53)."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from app.services.publish_schedule import (
    env_slots,
    next_scheduled,
    parse_slots,
    slot_env_name,
)


UTC = timezone.utc


def _ts(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


def test_parse_slots_roundtrip():
    slots = parse_slots("08:00-10:00, 18:00-22:00")
    assert slots == [(time(8), time(10)), (time(18), time(22))]


def test_parse_slots_empty_and_garbage():
    assert parse_slots("") == []
    assert parse_slots(None) == []
    assert parse_slots("   ") == []
    # malformed entries dropped, valid ones kept
    assert parse_slots("25:00-26:00, junk, 18:00-22:00, 22:00-22:00, 10:00-9:00") == [
        (time(18), time(22))
    ]


def test_slot_env_name():
    assert slot_env_name("tiktok") == "PUBLISH_SLOT_TIKTOK"
    assert slot_env_name(" Instagram ") == "PUBLISH_SLOT_INSTAGRAM"


def test_env_slots_reads_platform_var(monkeypatch):
    monkeypatch.setenv("PUBLISH_SLOT_TIKTOK", "18:00-22:00")
    monkeypatch.delenv("PUBLISH_SLOT_INSTAGRAM", raising=False)
    assert env_slots("tiktok") == [(time(18), time(22))]
    assert env_slots("instagram") == []


def test_no_slot_means_immediately_available():
    now = _ts(13, 21, 45)
    assert next_scheduled("instagram", now, slots=[]) == now


def test_inside_slot_schedules_now():
    slots = [(time(18), time(22))]
    now = _ts(13, 19)
    assert next_scheduled("tiktok", now, slots=slots) == now


def test_before_slot_schedules_slot_start():
    slots = [(time(18), time(22))]
    now = _ts(13, 9, 30)
    assert next_scheduled("tiktok", now, slots=slots) == _ts(13, 18)


def test_after_slot_schedules_tomorrow_start():
    slots = [(time(18), time(22))]
    now = _ts(13, 22, 30)
    assert next_scheduled("tiktok", now, slots=slots) == _ts(14, 18)


def test_multiple_slots_pick_next_window():
    slots = [(time(8), time(10)), (time(18), time(22))]
    now = _ts(13, 11)
    assert next_scheduled("tiktok", now, slots=slots) == _ts(13, 18)
    now = _ts(13, 4)
    assert next_scheduled("tiktok", now, slots=slots) == _ts(13, 8)


def test_naive_now_gets_utc():
    slots = [(time(18), time(22))]
    naive = datetime(2026, 10, 13, 19, 0)
    out = next_scheduled("tiktok", naive, slots=slots)
    assert out.tzinfo == UTC
    assert out == naive.replace(tzinfo=UTC)


def test_env_override_used_when_slots_none(monkeypatch):
    monkeypatch.setenv("PUBLISH_SLOT_TIKTOK", "06:00-08:00")
    now = _ts(13, 7)
    assert next_scheduled("tiktok", now) == now  # inside 06:00-08:00
    now = _ts(13, 9)
    assert next_scheduled("tiktok", now) == _ts(14, 6)


def test_far_future_window_holds_job_back(monkeypatch):
    """A job scheduled for a far window is far in the future — the claim query
    (`available_at <= now`) must not hand it to a worker before its hour."""
    monkeypatch.setenv("PUBLISH_SLOT_TIKTOK", "23:00-23:30")
    now = _ts(13, 9)
    scheduled = next_scheduled("tiktok", now)
    assert scheduled == _ts(13, 23)
    assert now < scheduled and scheduled <= now + timedelta(hours=14)