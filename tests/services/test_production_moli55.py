"""MOLI-55: b-roll marks, render_spec blocks, and YouTube analytics calibration."""
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.no_db

from app.services.clip_analytics import (
    apply_analytics,
    build_analytics_payload,
    calibrate_clip_score,
    fetch_youtube_analytics,
    merge_analytics,
    parse_retention_report,
    parse_views_report,
    refresh_access_token,
    should_refresh,
    youtube_video_id,
)
from app.services.production import (
    BROLL_PROMPT_HINT,
    normalize_broll_marks,
    production_blocks,
    with_broll_hint,
)
from app.services.rules.render_spec import build_render_spec
from app.services.rules.schema import RuleSet
from datetime import datetime, timedelta, timezone


def _camp(production=None, ruleset=None):
    meta = {"ruleset": (ruleset or RuleSet()).model_dump()}
    if production:
        meta["production"] = production
    return SimpleNamespace(id=1, source_metadata=meta)


def _cand(marks=None):
    extra = {}
    if marks is not None:
        extra["broll_marks"] = marks
    return SimpleNamespace(start_time=10.0, end_time=30.0, extra_metadata=extra)


def test_blocks_default_off_and_are_independent():
    out = build_render_spec(_camp(), SimpleNamespace(extra_metadata={}), _cand())
    assert out["broll"]["enabled"] is False and out["broll"]["marks"] == []
    assert out["music"]["enabled"] is False
    assert out["transitions"]["enabled"] is False
    assert out["sfx"]["enabled"] is False
    assert "suppressed_reason" not in out["music"]

    camp = _camp({"broll": {"enabled": True}, "transitions": {"enabled": True, "effect": "wipeleft"}})
    marks = [{"start": 2.0, "end": 5.0, "query": "city skyline", "placement": "overlay"}]
    out = build_render_spec(camp, SimpleNamespace(extra_metadata={}), _cand(marks))
    assert out["broll"]["enabled"] is True
    assert out["broll"]["marks"] == [{
        "start": 2.0, "end": 5.0, "query": "city skyline", "placement": "overlay",
    }]
    assert out["transitions"]["enabled"] is True and out["transitions"]["effect"] == "wipeleft"
    assert out["music"]["enabled"] is False
    assert out["sfx"]["enabled"] is False


def test_original_only_suppresses_music_even_when_requested():
    rs = RuleSet()
    rs.audio.original_only = True
    camp = _camp({"music": {"enabled": True, "track": "bed_soft"}}, ruleset=rs)
    blocks = production_blocks(camp, _cand(), original_only=True, no_added_music=True)
    assert blocks["music"]["enabled"] is False
    assert blocks["music"]["requested"] is True
    assert blocks["music"]["suppressed_reason"] == "audio.original_only"
    spec = build_render_spec(camp, SimpleNamespace(extra_metadata={}), _cand())
    assert spec["audio"]["original_only"] is True
    assert spec["music"]["enabled"] is False
    assert spec["music"]["suppressed_reason"] == "audio.original_only"


def test_no_added_music_suppresses_when_original_only_is_off():
    blocks = production_blocks(
        _camp({"music": {"enabled": True}}),
        _cand(),
        original_only=False,
        no_added_music=True,
    )
    assert blocks["music"]["enabled"] is False
    assert blocks["music"]["suppressed_reason"] == "audio.no_added_music"


def test_broll_marks_shift_absolute_times_and_drop_junk():
    marks = normalize_broll_marks(
        [
            {"start": 102, "end": 106, "query": "night traffic"},
            {"start": 1, "end": 1.1, "query": "too short"},
            {"start": 4, "end": 9, "query": "overlaps the first once shifted"},
            "nope",
            {"start": 8, "end": 12, "query": "  desk   setup  ", "placement": "cut"},
        ],
        clip_duration=20,
        source_start=100,
        limit=4,
    )
    assert marks[0]["start"] == 2.0 and marks[0]["end"] == 6.0
    assert marks[0]["query"] == "night traffic"
    assert marks[1] == {"start": 8.0, "end": 12.0, "query": "desk setup", "placement": "cut"}
    assert len(marks) == 2


def test_broll_hint_is_off_unless_asked():
    base = "Rules\nTranscript segments (start,end,text) for this chunk only:\n"
    assert with_broll_hint(base, False) == base
    hinted = with_broll_hint(base, True)
    assert hinted.startswith("Rules\n" + BROLL_PROMPT_HINT)
    assert hinted.endswith("Transcript segments (start,end,text) for this chunk only:\n")


def test_parse_clips_keeps_broll_marks_on_the_candidate():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "grok_clip_decider_tick.py"
    spec = importlib.util.spec_from_file_location("grok_clip_decider_tick", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    parsed = mod.parse_clips(
        {"clips": [{
            "start": 10, "end": 30, "title": "t", "reason": "r",
            "broll": [{"start": 2, "end": 5, "query": "keyboard closeup", "placement": "overlay"}],
        }]},
        dmin=15, dmax=45,
    )
    assert parsed[0]["extra"]["broll_marks"][0]["query"] == "keyboard closeup"
    plain = mod.build_decider_prompt(
        dmin=15, dmax=45, duration=90, brief="", copy_rules="",
    )
    assert "broll" not in plain
    assert "broll" in mod.build_decider_prompt(
        dmin=15, dmax=45, duration=90, brief="", copy_rules="", ask_broll=True,
    )


def test_youtube_video_id_and_calibration():
    assert youtube_video_id("https://www.youtube.com/shorts/abcdefghijk") == "abcdefghijk"
    assert youtube_video_id("https://youtu.be/abcdefghijk?t=3") == "abcdefghijk"
    assert youtube_video_id("https://www.youtube.com/watch?v=abcdefghijk") == "abcdefghijk"
    assert youtube_video_id("https://example.com/watch?v=abcdefghijk") is None
    # Mid prior, modest retention, few views: calibration moves, it does not jump to the metric.
    tuned = calibrate_clip_score(0.80, views=100, average_view_percentage=40)
    assert tuned is not None and 0.5 < tuned < 0.8
    assert calibrate_clip_score(0.80, views=None, average_view_percentage=None) is None
    assert calibrate_clip_score(None, views=10_000, average_view_percentage=100) == 1.0


def test_analytics_payload_is_stored_on_the_clip_and_candidate():
    metrics = parse_views_report({
        "columnHeaders": [
            {"name": "views"},
            {"name": "averageViewDuration"},
            {"name": "averageViewPercentage"},
        ],
        "rows": [[1200, 8.5, 42.0]],
    })
    retention = parse_retention_report({
        "columnHeaders": [
            {"name": "elapsedVideoTimeRatio"},
            {"name": "audienceWatchRatio"},
        ],
        "rows": [[0.0, 1.0], [0.5, 0.42]],
    })
    payload = build_analytics_payload(
        video_id="abcdefghijk",
        metrics=metrics,
        retention=retention,
        prior_score=0.8,
        fetched_at="2026-10-14T12:00:00+00:00",
    )
    assert payload["youtube"]["views"] == 1200
    assert payload["youtube"]["retention"][1]["watch_ratio"] == 0.42
    assert payload["clip_score"]["prior"] == 0.8
    assert payload["clip_score"]["calibrated"] is not None

    class Clip:
        analytics = {}

    class Candidate:
        score = 0.8
        extra_metadata = {"title": "keep me"}

    clip, cand = Clip(), Candidate()
    apply_analytics(clip, cand, payload)
    assert clip.analytics["youtube"]["video_id"] == "abcdefghijk"
    assert cand.score == 0.8
    assert cand.extra_metadata["title"] == "keep me"
    assert cand.extra_metadata["calibrated_clip_score"] == payload["clip_score"]["calibrated"]
    assert cand.extra_metadata["clip_score_prior"] == 0.8

    empty = build_analytics_payload(
        video_id="abcdefghijk",
        metrics={"views": None, "average_view_percentage": None, "average_view_duration_seconds": None},
        retention=[],
        prior_score=0.8,
        fetched_at="2026-10-15T12:00:00+00:00",
        error="no_rows",
    )
    merged = merge_analytics(clip.analytics, empty)
    assert merged["youtube"]["views"] == 1200
    assert merged["last_error"] == "no_rows"
    now = datetime(2026, 10, 14, 18, tzinfo=timezone.utc)
    assert should_refresh(payload, now, refresh_hours=24) is False
    assert should_refresh(payload, now + timedelta(hours=25), refresh_hours=24) is True


def test_fetch_uses_the_analytics_reports_contract():
    seen = []

    def _get(url, params, headers):
        seen.append((url, params, headers))
        if "audienceWatchRatio" in params["metrics"]:
            return {"columnHeaders": [{"name": "elapsedVideoTimeRatio"}, {"name": "audienceWatchRatio"}], "rows": [[0.2, 0.7]]}
        return {"columnHeaders": [{"name": "views"}, {"name": "averageViewDuration"}, {"name": "averageViewPercentage"}], "rows": [[10, 3, 50]]}

    metrics, retention, error = fetch_youtube_analytics(
        "abcdefghijk",
        access_token="tok",
        start_date="2026-01-01",
        end_date="2026-10-14",
        get=_get,
    )
    assert error is None and metrics["views"] == 10 and retention[0]["watch_ratio"] == 0.7
    assert seen[0][2]["Authorization"] == "Bearer tok"
    assert seen[0][1]["filters"] == "video==abcdefghijk"
    assert refresh_access_token(
        client_id="id", client_secret="sec", refresh_token="ref",
        post=lambda url, data: {"access_token": "ya"},
    ) == "ya"
