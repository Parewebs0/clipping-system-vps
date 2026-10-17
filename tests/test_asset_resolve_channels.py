"""Streamer channels (YouTube / Twitch / Kick) -> latest VODs; single VODs as direct sources.

yt-dlp and the Kick listing are mocked: no network in these tests.
"""
from __future__ import annotations

import io
import json
import subprocess
from datetime import datetime, timezone

import pytest

import app.services.asset_resolve as ar

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
DAY = 86400


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("RESOLVE_CHANNELS_ENABLED", "RESOLVE_CHANNEL_VODS", "RESOLVE_CHANNEL_MAX_AGE_DAYS",
              "RESOLVE_CHANNEL_MAX_DURATION_S", "RESOLVE_CHANNEL_MIN_DURATION_S", "RESOLVE_CHANNEL_SCAN",
              "RESOLVE_YTDLP_BIN", "RESOLVE_YTDLP_TIMEOUT_S"):
        monkeypatch.delenv(k, raising=False)

    def _no_network(*a, **k):
        raise AssertionError("network call in test")

    monkeypatch.setattr(ar.urllib.request, "urlopen", _no_network)


def ts(days_ago: float) -> int:
    return int(NOW.timestamp() - days_ago * DAY)


@pytest.mark.parametrize("url,kind", [
    ("https://www.youtube.com/@jackneel", "channel"),
    ("https://youtube.com/@totalwar/", "channel"),
    ("https://www.youtube.com/@jackneel/videos", "channel"),
    ("https://www.youtube.com/channel/UCabc123/streams", "channel"),
    ("https://www.youtube.com/c/SomeName", "channel"),
    ("https://www.twitch.tv/clix", "channel"),
    ("https://twitch.tv/clix/videos?filter=archives", "channel"),
    ("https://kick.com/gattouz0", "channel"),
    ("https://kick.com/gattouz0/videos", "channel"),
    ("https://www.twitch.tv/videos/2898980714", "twitch_vod"),
    ("https://kick.com/gattouz0/videos/21e34232-613c-4c6b-9e5e-413ea2bb1392", "kick_vod"),
    ("https://www.twitch.tv/directory", "unknown"),
    ("https://kick.com/", "unknown"),
    ("https://youtube.com/source/E0n2A-DUy3Y/shorts", "social"),
    ("https://www.youtube.com/watch?v=abcdefgh", "youtube"),
])
def test_classify(url, kind):
    assert ar.classify(url) == kind


def test_channels_disabled_by_env_fall_back_to_old_kinds(monkeypatch):
    monkeypatch.setenv("RESOLVE_CHANNELS_ENABLED", "0")
    assert ar.classify("https://kick.com/gattouz0") == "unknown"
    assert ar.classify("https://www.youtube.com/@jackneel") == "profile"


def test_channel_ref_listing_urls():
    assert ar.channel_ref("https://www.youtube.com/@x") == ("youtube", "https://www.youtube.com/@x/videos")
    assert ar.channel_ref("https://www.youtube.com/@x/streams") == ("youtube", "https://www.youtube.com/@x/streams")
    assert ar.channel_ref("https://twitch.tv/clix") == (
        "twitch", "https://www.twitch.tv/clix/videos?filter=archives&sort=time")
    assert ar.channel_ref("https://kick.com/gattouz0/") == ("kick", "gattouz0")


def test_single_vods_are_direct_sources():
    items, err = ar.expand_url("https://www.twitch.tv/videos/2898980714")
    assert err is None and items[0]["source_provider"] == "twitch_vod" and items[0]["source_id"] == "2898980714"
    items, err = ar.expand_url("https://kick.com/gattouz0/videos/21e34232-613c-4c6b-9e5e-413ea2bb1392")
    assert err is None and items[0]["source_provider"] == "kick_vod"
    assert items[0]["source_id"] == "21e34232-613c-4c6b-9e5e-413ea2bb1392"


def _fake_run(payload, calls):
    def run(cmd, capture_output, text, timeout):
        calls.append({"cmd": cmd, "timeout": timeout})
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(payload), stderr="")
    return run


def test_youtube_channel_picks_two_recent_finished_vods(monkeypatch):
    entries = [
        {"id": "live1", "duration": None, "timestamp": ts(0), "live_status": "is_live"},
        {"id": "a", "title": "A", "duration": 3600, "timestamp": ts(1), "live_status": "was_live",
         "webpage_url": "https://www.youtube.com/watch?v=a"},
        {"id": "long", "duration": 5 * 3600, "timestamp": ts(2)},
        {"id": "short", "duration": 30, "timestamp": ts(2)},
        {"id": "b", "duration": 7200, "upload_date": "20261014"},
        {"id": "c", "duration": 1800, "timestamp": ts(3)},
    ]
    calls = []
    monkeypatch.setattr(ar.subprocess, "run", _fake_run({"entries": entries}, calls))
    items, err = ar.resolve_channel_vods("https://www.youtube.com/@x", now=NOW)
    assert err is None
    assert [i["source_id"] for i in items] == ["youtube:a", "youtube:b"]
    assert items[1]["source_url"] == "https://www.youtube.com/watch?v=b"
    assert items[0]["source_provider"] == "youtube" and items[0]["kind"] == "channel_vod"
    cmd = calls[0]["cmd"]
    assert cmd[0] == "yt-dlp" and "--skip-download" in cmd and "-J" in cmd
    assert cmd[cmd.index("--playlist-items") + 1] == "1:6"
    assert cmd[-1] == "https://www.youtube.com/@x/videos"


def test_env_knobs(monkeypatch):
    monkeypatch.setenv("RESOLVE_CHANNEL_VODS", "1")
    monkeypatch.setenv("RESOLVE_CHANNEL_MAX_AGE_DAYS", "2")
    monkeypatch.setenv("RESOLVE_CHANNEL_MAX_DURATION_S", "1000")
    monkeypatch.setenv("RESOLVE_YTDLP_BIN", "/opt/yt-dlp")
    monkeypatch.setenv("RESOLVE_YTDLP_TIMEOUT_S", "42")
    entries = [
        {"id": "old", "duration": 600, "timestamp": ts(3)},
        {"id": "big", "duration": 2000, "timestamp": ts(1)},
        {"id": "ok", "duration": 600, "timestamp": ts(1)},
        {"id": "ok2", "duration": 600, "timestamp": ts(1)},
    ]
    calls = []
    monkeypatch.setattr(ar.subprocess, "run", _fake_run({"entries": entries}, calls))
    items, err = ar.resolve_channel_vods("https://twitch.tv/clix", now=NOW)
    assert [i["source_id"] for i in items] == ["twitch:ok"]
    assert calls[0]["cmd"][0] == "/opt/yt-dlp" and calls[0]["timeout"] == 42
    assert calls[0]["cmd"][calls[0]["cmd"].index("--playlist-items") + 1] == "1:3"


def test_twitch_ids_and_urls(monkeypatch):
    entries = [{"id": "v123", "duration": 3600, "timestamp": ts(1)}]
    monkeypatch.setattr(ar.subprocess, "run", _fake_run({"entries": entries}, []))
    items, err = ar.resolve_channel_vods("https://www.twitch.tv/clix", now=NOW)
    assert items[0]["source_id"] == "twitch:123"
    assert items[0]["source_url"] == "https://www.twitch.tv/videos/123"
    assert items[0]["source_provider"] == "twitch_vod"


def test_no_recent_vods_and_listing_failure(monkeypatch):
    monkeypatch.setattr(ar.subprocess, "run", _fake_run({"entries": [{"id": "x", "duration": 60, "timestamp": ts(30)}]}, []))
    assert ar.resolve_channel_vods("https://www.youtube.com/@x", now=NOW) == ([], "channel_no_recent_vods")

    def boom(*a, **k):
        raise subprocess.TimeoutExpired("yt-dlp", 1)

    monkeypatch.setattr(ar.subprocess, "run", boom)
    items, err = ar.resolve_channel_vods("https://www.youtube.com/@x", now=NOW)
    assert items == [] and err == "channel_listing_failed:TimeoutExpired"
    found, errors = ar.expand_all(["https://www.youtube.com/@x"])
    assert found == [] and errors == ["channel_listing_failed:TimeoutExpired:https://www.youtube.com/@x"]


def test_kick_channel_uses_public_listing(monkeypatch):
    payload = [
        {"is_live": True, "video": {"uuid": "live-uuid-0000"}, "duration": 0, "start_time": "2026-10-17 10:00:00"},
        {"is_live": False, "session_title": "GATTOUZ gaming", "duration": 6644000,
         "start_time": "2026-10-15 16:04:03", "video": {"uuid": "21e34232-613c-4c6b-9e5e-413ea2bb1392"}},
        {"is_live": False, "session_title": "old", "duration": 3600000,
         "start_time": "2026-09-01 10:00:00", "video": {"uuid": "aaaaaaaa-old"}},
    ]
    seen = []

    def fake_urlopen(req, timeout):
        seen.append(req.full_url)
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(ar.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(ar.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no yt-dlp")))
    items, err = ar.resolve_channel_vods("https://kick.com/gattouz0", now=NOW)
    assert seen == ["https://kick.com/api/v2/channels/gattouz0/videos"]
    assert err is None and len(items) == 1
    assert items[0]["source_url"] == "https://kick.com/gattouz0/videos/21e34232-613c-4c6b-9e5e-413ea2bb1392"
    assert items[0]["source_provider"] == "kick_vod" and items[0]["name"] == "GATTOUZ gaming"


def test_campaign8_style_kick_only_resolves(monkeypatch):
    from datetime import timedelta
    start = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    payload = [{"is_live": False, "session_title": "s", "duration": 3600000,
                "start_time": start, "video": {"uuid": "11111111-2222"}}]
    monkeypatch.setattr(ar.urllib.request, "urlopen", lambda req, timeout: io.BytesIO(json.dumps(payload).encode()))
    found, errors = ar.expand_all(["https://docs.google.com/document/d/x/edit", "https://kick.com/gattouz0"])
    assert errors == [] and [f["source_id"] for f in found] == ["kick:11111111-2222"]
