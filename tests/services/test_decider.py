"""MOLI-48: full-video chunking, IoU merge, phrase snap, sub-scores."""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_db

from app.services.decider import (
    MAX_SEGMENT_BLOCK_CHARS,
    DeciderOptions,
    chunk_segments,
    decider_options,
    merge_by_iou,
    snap_window,
    window_iou,
)
from app.services.publish_copy import clip_score

ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = ROOT / "reports" / "moli-48"


def _tick():
    path = ROOT / "scripts" / "grok_clip_decider_tick.py"
    spec = importlib.util.spec_from_file_location("grok_clip_decider_tick", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def long_transcript(*, minutes: float = 40.0, seg_s: float = 5.0) -> list[dict]:
    """Synthetic WhisperX-style transcript longer than 30 minutes."""
    segs: list[dict] = []
    t = 0.0
    n = int(minutes * 60 / seg_s)
    mid = n // 2
    for i in range(n):
        if i == mid:
            text = "SECOND_HALF_MARKER this is the late hook of the interview we wanted."
        elif i < 4:
            text = f"Opening remarks number {i} about the product launch today."
        else:
            text = f"Segment {i:04d} continues the conversation with more detail here."
        toks = text.split()
        step = seg_s / max(len(toks), 1)
        words = []
        wt = t
        for k, tok in enumerate(toks):
            token = tok if k < len(toks) - 1 else tok.rstrip(".,") + "."
            words.append({"start": round(wt, 3), "end": round(wt + step * 0.85, 3), "word": token})
            wt += step
        segs.append({"start": round(t, 3), "end": round(t + seg_s, 3), "text": text, "words": words})
        t += seg_s
    return segs


def _llm_from_chunk(prompt: str, _meta: dict | None = None) -> dict:
    """Return 3 windows inside whatever times the prompt actually shows."""
    body = prompt.split("Transcript segments", 1)[-1]
    times = [(float(a), float(b)) for a, b in re.findall(r"(\d+\.\d+)-(\d+\.\d+)", body)]
    if not times:
        return {"clips": []}
    t0, t1 = times[0][0], times[-1][1]
    span = max(t1 - t0, 20.0)
    clips = []
    for i, hook in enumerate((0.9, 0.55, 0.3)):
        start = t0 + (span * i / 6.0)
        end = start + 20.0
        if end > t1 + 1:
            end = min(t1, start + 18.0)
        if end - start < 8:
            continue
        marker = "SECOND_HALF_MARKER" in prompt
        clips.append({
            "start": round(start, 2),
            "end": round(end, 2),
            "reason": "second half" if marker else "first half",
            "title": f"{'late' if marker else 'early'} hook {i}",
            "caption": "ForgeGUI builds a shop UI.\n#forgeguipartner",
            "on_screen_text": "ForgeGUI",
            "hook": hook,
            "flow": 0.4 + i * 0.15,
            "value": 0.8 - i * 0.1,
            "fit": 0.7,
        })
    return {"clips": clips}


def test_chunk_segments_cover_the_second_half():
    segs = long_transcript()
    assert segs[-1]["end"] >= 30 * 60
    legacy = chunk_segments(segs, DeciderOptions(chunk_chars=0))
    assert len(legacy) == 1
    assert "SECOND_HALF_MARKER" not in legacy[0]["block"]
    assert len(legacy[0]["block"]) <= MAX_SEGMENT_BLOCK_CHARS

    full = chunk_segments(segs)
    assert len(full) > 1
    assert any("SECOND_HALF_MARKER" in ch["block"] for ch in full)
    assert all(len(ch["block"]) <= MAX_SEGMENT_BLOCK_CHARS for ch in full)
    # 10 % overlap: consecutive chunks share at least one segment.
    assert full[0]["segments"][-1]["start"] >= full[1]["start"] or (
        full[1]["start"] < full[0]["end"]
    )


def test_prompts_stay_under_the_segment_cap():
    mod = _tick()
    segs = long_transcript()
    opts = DeciderOptions()
    chunks = chunk_segments(segs, opts)
    seen = []

    def _call(prompt: str, meta: dict) -> dict:
        assert meta["segment_chars"] <= MAX_SEGMENT_BLOCK_CHARS
        assert len(prompt) > meta["segment_chars"]
        seen.append(meta)
        return _llm_from_chunk(prompt, meta)

    clips = mod.collect_clips(
        segments=segs,
        dmin=15,
        dmax=45,
        duration=segs[-1]["end"],
        brief="- Hook in 2s",
        copy_rules="- No campaign copy rules.",
        options=opts,
        call_llm=_call,
        ruleset=None,
        retry_enabled=False,
    )
    assert len(seen) == len(chunks)
    assert clips
    assert any(c["start"] >= segs[-1]["end"] / 2 for c in clips)


def test_legacy_options_miss_the_second_half():
    mod = _tick()
    segs = long_transcript()
    opts = DeciderOptions(chunk_chars=0, candidates_per_chunk=2, top_n=2)
    clips = mod.collect_clips(
        segments=segs,
        dmin=15,
        dmax=45,
        duration=segs[-1]["end"],
        brief="",
        copy_rules="",
        options=opts,
        call_llm=_llm_from_chunk,
        ruleset=None,
        retry_enabled=False,
    )
    assert clips
    assert all(c["start"] < segs[-1]["end"] / 2 for c in clips)


def test_iou_merge_drops_overlap_keeps_higher_score():
    kept = merge_by_iou(
        [
            {"start": 10, "end": 30, "score": 0.9},
            {"start": 12, "end": 32, "score": 0.4},
            {"start": 80, "end": 100, "score": 0.5},
        ],
        threshold=0.5,
    )
    assert [(c["start"], c["end"]) for c in kept] == [(10, 30), (80, 100)]
    assert window_iou((10, 30), (12, 32)) > 0.5


def test_snap_window_lands_on_word_or_sentence_end():
    segs = [
        {
            "start": 0.0,
            "end": 8.0,
            "text": "Hello there. We continue talking now.",
            "words": [
                {"start": 0.0, "end": 0.8, "word": "Hello"},
                {"start": 0.9, "end": 1.6, "word": "there."},
                {"start": 2.0, "end": 2.4, "word": "We"},
                {"start": 2.5, "end": 3.4, "word": "continue"},
                {"start": 3.5, "end": 4.3, "word": "talking"},
                {"start": 4.4, "end": 5.2, "word": "now."},
            ],
        }
    ]
    start, end = snap_window(0.4, 4.9, segs)
    words = {(w["start"], w["end"]) for w in segs[0]["words"]}
    assert any(abs(start - ws) < 1e-6 for ws, _ in words)
    assert any(abs(end - we) < 1e-6 for _, we in words)
    # Prefer sentence bounds when they sit within 2s.
    start, end = snap_window(0.5, 1.2, segs)
    assert start == 0.0
    assert end == 1.6


def test_subscores_are_stored_and_ranking_varies():
    mod = _tick()
    segs = long_transcript(minutes=8)
    clips = mod.collect_clips(
        segments=segs,
        dmin=15,
        dmax=45,
        duration=segs[-1]["end"],
        brief="",
        copy_rules="",
        options=DeciderOptions(top_n=8),
        call_llm=_llm_from_chunk,
        ruleset=None,
        retry_enabled=False,
    )
    assert len(clips) >= 2
    scores = [c["score"] for c in clips]
    assert len(set(scores)) > 1
    for c in clips:
        ss = c.get("subscores") or {}
        assert ss
        assert c["score"] == clip_score(
            c["end"] - c["start"], 15, 45, None, ss
        )


def test_decider_options_chunk_chars_zero_is_legacy():
    from types import SimpleNamespace

    opts = decider_options(SimpleNamespace(source_metadata={"decider": {"chunk_chars": 0}}))
    assert opts.chunk_chars == 0
    assert decider_options().chunk_chars == 4000


def test_before_after_same_long_video(tmp_path):
    """Acceptance: candidate lists of the same 40 min fixture, old vs new."""
    mod = _tick()
    segs = long_transcript()
    duration = segs[-1]["end"]
    kwargs = dict(
        segments=segs,
        dmin=15,
        dmax=45,
        duration=duration,
        brief="",
        copy_rules="",
        call_llm=_llm_from_chunk,
        ruleset=None,
        retry_enabled=False,
    )
    before = mod.collect_clips(options=DeciderOptions(chunk_chars=0, candidates_per_chunk=2, top_n=2), **kwargs)
    after = mod.collect_clips(options=DeciderOptions(), **kwargs)
    assert after
    assert any(c["start"] >= duration / 2 for c in after)
    assert all(c["start"] < duration / 2 for c in before)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "asset": "fixture:long_transcript_40min",
        "duration_s": duration,
        "before": [{"start": c["start"], "end": c["end"], "score": c["score"], "title": c.get("title")} for c in before],
        "after": [{"start": c["start"], "end": c["end"], "score": c["score"], "title": c.get("title"), "subscores": c.get("subscores")} for c in after],
    }
    (REPORT_DIR / "before-after.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    lines = [
        "# MOLI-48 before / after — same 40 min fixture",
        "",
        f"Duration: {duration:.0f}s.",
        "",
        "## Before (`chunk_chars=0`, first 5 000 chars, top 2)",
        "",
        "| start | end | score | title |",
        "| --- | --- | --- | --- |",
    ]
    for c in payload["before"]:
        lines.append(f"| {c['start']:.1f} | {c['end']:.1f} | {c['score']} | {c['title']} |")
    lines += ["", "## After (chunked full video, top 8)", "", "| start | end | score | title |", "| --- | --- | --- | --- |"]
    for c in payload["after"]:
        lines.append(f"| {c['start']:.1f} | {c['end']:.1f} | {c['score']} | {c['title']} |")
    (REPORT_DIR / "before-after.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    _draw_timeline(REPORT_DIR / "before-after.png", duration, payload["before"], payload["after"])
    assert (REPORT_DIR / "before-after.png").is_file()


def _draw_timeline(path: Path, duration: float, before: list[dict], after: list[dict]) -> None:
    from PIL import Image, ImageDraw, ImageFont

    w, h = 1280, 360
    img = Image.new("RGB", (w, h), (18, 18, 22))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    margin = 60
    bar_w = w - 2 * margin

    def _x(t: float) -> int:
        return margin + int(bar_w * min(1.0, max(0.0, t / duration)))

    draw.text((margin, 16), "Before (legacy first window)", fill=(200, 200, 200), font=font)
    draw.rectangle([margin, 48, margin + bar_w, 88], outline=(80, 80, 90))
    for c in before:
        draw.rectangle([_x(c["start"]), 50, max(_x(c["end"]), _x(c["start"]) + 4), 86], fill=(180, 70, 70))
    draw.text((margin, 120), "After (full video, chunked)", fill=(200, 200, 200), font=font)
    draw.rectangle([margin, 152, margin + bar_w, 192], outline=(80, 80, 90))
    for c in after:
        draw.rectangle([_x(c["start"]), 154, max(_x(c["end"]), _x(c["start"]) + 4), 190], fill=(70, 160, 110))
    draw.text((margin, 220), "0:00", fill=(140, 140, 150), font=font)
    draw.text((_x(duration / 2) - 20, 220), f"{duration/120:.0f}:00", fill=(140, 140, 150), font=font)
    draw.text((margin + bar_w - 40, 220), f"{duration/60:.0f}:00", fill=(140, 140, 150), font=font)
    draw.text((margin, 280), "Red = candidates the old 5 000-char cap could see. Green = full-video chunks.", fill=(160, 160, 170), font=font)
    img.save(path)
