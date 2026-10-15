"""Live decider prompt/parse stay pure and keep #65 hook_end behaviour."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db


def _mod():
    path = Path(__file__).resolve().parents[2] / "scripts" / "grok_clip_decider_tick.py"
    spec = importlib.util.spec_from_file_location("grok_clip_decider_tick", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_build_decider_prompt_slices_segments_only():
    from app.services.decider import MAX_SEGMENT_BLOCK_CHARS, chunk_segments

    mod = _mod()
    segs = [{"start": 0, "end": 1, "text": "hello " + ("x" * 6000)}]
    chunks = chunk_segments(segs)
    assert chunks
    block = chunks[0]["block"]
    assert len(block) <= MAX_SEGMENT_BLOCK_CHARS
    prompt = (
        mod.build_decider_prompt(
            dmin=15,
            dmax=45,
            duration=90,
            brief="",
            copy_rules="- No campaign copy rules.",
        )
        + block
    )
    assert "duration_min=15" in prompt
    prefix = mod.build_decider_prompt(
        dmin=15,
        dmax=45,
        duration=90,
        brief="",
        copy_rules="- No campaign copy rules.",
    )
    assert len(prompt) - len(prefix) == len(block)
    assert len(block) == MAX_SEGMENT_BLOCK_CHARS


def test_parse_clips_matches_live_filters():
    mod = _mod()
    data = {
        "clips": [
            {"start": 0, "end": 1, "title": "too short", "reason": "nope"},
            {
                "start": 0,
                "end": 50,
                "title": "long",
                "caption": "cap",
                "on_screen_text": "hi",
                "hook_end": 1.5,
                "reason": "ok",
            },
            {"start": 10, "end": 30, "title": "third is dropped"},
        ]
    }
    parsed = mod.parse_clips(data, dmin=15, dmax=45)
    assert len(parsed) == 2
    clip = parsed[0]
    assert clip["start"] == 0.0
    assert clip["end"] == 45.0  # clamped to dmax
    from app.services.publish_copy import clip_score

    assert clip["score"] == clip_score(45, 15, 45, None)
    assert clip["score"] != 0.7
    assert clip["extra"]["hook_end"] == 1.5
    assert clip["extra"]["source"] == "grok_clip_decider"
    assert clip["extra"]["keywords"] == []
    assert clip["extra"]["emojis"] == []
    assert parsed[1]["start"] == 10.0


def test_parse_clips_keeps_keywords_and_emojis():
    mod = _mod()
    prompt = mod.build_decider_prompt(
        dmin=15, dmax=45, duration=90, brief="- Hook", copy_rules="- none",
    )
    assert "keywords" in prompt and "emojis" in prompt
    assert "Do not put emoji characters in on_screen_text" in prompt
    data = {"clips": [{
        "start": 0, "end": 20, "title": "t", "caption": "c", "reason": "r",
        "keywords": ["casita", "", "x" * 50, "folds"],
        "emojis": [{"emoji": "🔥", "keyword": "casita"}, "nope", {"emoji": ""}],
    }]}
    parsed = mod.parse_clips(data, dmin=15, dmax=45)
    assert parsed[0]["extra"]["keywords"] == ["casita", "folds"]
    assert parsed[0]["extra"]["emojis"] == [{"emoji": "🔥", "keyword": "casita"}]
