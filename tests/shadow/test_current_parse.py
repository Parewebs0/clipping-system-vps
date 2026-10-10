"""Live decider prompt/parse stay pure and keep #65 hook_end behaviour."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _mod():
    path = Path(__file__).resolve().parents[2] / "scripts" / "grok_clip_decider_tick.py"
    spec = importlib.util.spec_from_file_location("grok_clip_decider_tick", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_build_prompt_slices_segments_only():
    mod = _mod()
    segs = [{"start": 0, "end": 1, "text": "hello " + ("x" * 6000)}]
    prompt = mod.build_prompt(dmin=15, dmax=45, duration=90, brief="", segments=segs)
    assert "duration_min=15" in prompt
    assert "none beyond duration/format" in prompt
    prefix = mod.PROMPT.format(dmin=15, dmax=45, duration=90, brief="- (none beyond duration/format)")
    assert len(prompt) - len(prefix) == 5000


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
    assert len(parsed) == 1
    clip = parsed[0]
    assert clip["start"] == 0.0
    assert clip["end"] == 45.0  # clamped to dmax
    assert clip["score"] == 0.7
    assert clip["extra"]["hook_end"] == 1.5
    assert clip["extra"]["source"] == "grok_clip_decider"
