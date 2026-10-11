"""Decider copy: CopyRule goes into the prompt, check_copy accepts the caption.

Golden campaigns are the same rulesets tests/golden loads:
  #6 ForgeGUI and #18 Boxabl (fixtures in tests/fixtures/rules).
"""
import importlib.util
import json
from pathlib import Path

from app.services.publish_copy import (
    clip_score,
    copy_failures,
    copy_rules_for_prompt,
    copy_targets,
    review_clip_copies,
)
from app.services.rules.schema import load_ruleset
from app.services.rules.verifier import check_copy

FX = Path(__file__).resolve().parents[1] / "fixtures" / "rules"
ROOT = Path(__file__).resolve().parents[2]


def _ruleset(n: int):
    raw = json.loads((FX / f"ruleset_{n}.json").read_text(encoding="utf-8"))
    rs = load_ruleset({"ruleset": raw})
    assert rs is not None
    return rs


def _decider():
    path = ROOT / "scripts" / "grok_clip_decider_tick.py"
    spec = importlib.util.spec_from_file_location("grok_clip_decider_tick", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


FORGE_OK = {
    "start": 10.0,
    "end": 30.0,
    "reason": "shows the UI",
    "score": 0.4,
    "title": "ForgeGUI in a minute",
    "caption": "I used ForgeGUI to build this UI.\n#forgeguipartner #robloxdev #roblox",
    "on_screen_text": "ForgeGUI",
}
BOXABL_OK = {
    "start": 12.0,
    "end": 40.0,
    "reason": "the fold",
    "score": 0.55,
    "title": "Boxabl casita",
    "caption": "This tiny home is a Boxabl Casita.\n@boxabl",
    "on_screen_text": "foldable home",
}


def _passes(rs, clip) -> None:
    assert clip["copy_status"] == "pending"
    assert clip["copy_reason"] is None
    for platform in copy_targets(rs):
        failed = [c for c in check_copy(rs, platform, clip["title"], clip["caption"], clip["on_screen_text"]) if c["status"] == "fail"]
        assert failed == [], (platform, failed)


def test_prompt_states_forgegui_and_boxabl_copy_rules():
    forge = copy_rules_for_prompt(_ruleset(6))
    assert "#forgeguipartner #robloxdev #roblox" in forge
    assert "after the caption text" in forge
    assert "ForgeGUI" in forge
    assert "bait and switch" in forge
    assert "first element after the caption text" in forge

    box = copy_rules_for_prompt(_ruleset(18))
    assert "@boxabl" in box
    for platform in ("youtube", "tiktok", "instagram"):
        assert f"mentions ({platform})" in box
    assert "Boxabl" in box and "Kanye" in box
    assert "verbatim" not in box

    heaven = copy_rules_for_prompt(_ruleset(13))
    rs13 = _ruleset(13)
    assert rs13.copy_rules.exact_caption.strip() in heaven
    assert "#Ad" in heaven and "own line" in heaven
    assert "@SEGA_West" in heaven and "@sth_game" in heaven


def test_generated_copy_for_forgegui_and_boxabl_passes_check_copy():
    calls = {"n": 0}

    def _boom(_text):
        calls["n"] += 1
        raise AssertionError("retry")

    for n, good in ((6, FORGE_OK), (18, BOXABL_OK)):
        rs = _ruleset(n)
        out = review_clip_copies([dict(good)], rs, retry=_boom)
        assert calls["n"] == 0
        assert out[0]["copy_attempts"] == 1
        _passes(rs, out[0])


def test_retry_once_then_the_fixed_copy_passes():
    seen = {}

    def _retry(text):
        seen["text"] = text
        return [dict(FORGE_OK)]

    rs = _ruleset(6)
    bad = {"title": "Watch", "caption": "Look at this clip", "on_screen_text": "", "start": 1, "end": 20, "reason": "hook"}
    out = review_clip_copies([bad], rs, retry=_retry)
    assert "copy.hashtags" in seen["text"]
    assert "copy.ftc" in seen["text"]
    assert "copy.must_mention_any" in seen["text"]
    assert out[0]["copy_attempts"] == 2
    _passes(rs, out[0])


def test_second_failure_marks_needs_review_with_the_reason():
    def _retry(_text):
        return [{"title": "Still bad", "caption": "Nothing here", "on_screen_text": "Kanye", "reason": "no"}]

    rs = _ruleset(18)
    bad = {"title": "Hi", "caption": "A house", "on_screen_text": "", "reason": "window"}
    out = review_clip_copies([bad], rs, retry=_retry)
    assert out[0]["copy_status"] == "needs_review"
    assert out[0]["copy_attempts"] == 2
    assert "copy.must_mention_any" in out[0]["copy_reason"]
    assert "copy.mentions.tiktok" in out[0]["copy_reason"]
    assert "prohibitions.terms" in out[0]["copy_reason"]
    model = (ROOT / "app" / "models" / "candidate.py").read_text(encoding="utf-8")
    life = (ROOT / "app" / "services" / "candidate_lifecycle.py").read_text(encoding="utf-8")
    mig = (ROOT / "alembic" / "versions" / "0022_candidate_needs_review.py").read_text(encoding="utf-8")
    assert 'NEEDS_REVIEW = "needs_review"' in model
    assert "CandidateStatus.NEEDS_REVIEW.value" in life
    assert 'down_revision = "0021_clip_compliance"' in mig
    assert "UPDATE candidates SET status = 'pending' WHERE status = 'needs_review'" in mig


def test_boxabl_overlay_prohibition_fails_before_a_clean_retry():
    rs = _ruleset(18)
    dirty = dict(BOXABL_OK)
    dirty["on_screen_text"] = "Kanye on stage"
    fails = copy_failures(rs, dirty["title"], dirty["caption"], dirty["on_screen_text"])
    assert any(row["rule"] == "prohibitions.terms" for row in fails)

    out = review_clip_copies([dirty], rs, retry=lambda _t: [dict(BOXABL_OK)])
    _passes(rs, out[0])
    assert out[0]["copy_attempts"] == 2


def test_clip_score_follows_the_window_and_is_not_a_constant():
    # Midpoint of 15-45s is 30s → fit 1. Edge 15s is 1 - 15/45.
    mid = clip_score(30, 15, 45, None)
    edge = clip_score(15, 15, 45, None)
    assert mid == 1.0
    assert edge == 0.67
    assert mid != edge
    assert mid != 0.7 and edge != 0.7
    # Model score is averaged with the fit. A non-number is ignored.
    assert clip_score(30, 15, 45, 0.2) == 0.6
    assert clip_score(30, 15, 45, "later") == 1.0
    assert clip_score(30, 15, 45, True) == 1.0


def test_decider_prompt_carries_copy_rules_and_drops_the_fixed_score():
    mod = _decider()
    source = (ROOT / "scripts" / "grok_clip_decider_tick.py").read_text(encoding="utf-8")
    assert "score=0.7" not in source
    assert "clip_score(" in source
    assert "for YouTube" not in source
    rules = copy_rules_for_prompt(_ruleset(6))
    text = mod.build_decider_prompt(dmin=15, dmax=45, duration=90, brief="- Hook in 2s", copy_rules=rules)
    assert text.index("Copy rules") < text.index("#forgeguipartner") < text.index("Transcript")
    assert "for YouTube" not in text
    braced = mod.build_decider_prompt(dmin=15, dmax=45, duration=90, brief="use {hook}", copy_rules="tag {ad}")
    assert "{hook}" in braced and "{ad}" in braced
    again = mod.retry_prompt(text, "clip 0: copy.hashtags (orden distinto)")
    assert "clip 0: copy.hashtags" in again
    assert again.index("Failures:") > again.index("Copy rules")

    kept = mod.prepare_clips(
        [
            {"start": 0, "end": 1},
            {"start": 0, "end": 80, "caption": "too long"},
            {"start": "5", "end": "40"},
        ],
        15,
        45,
    )
    # The decider still looks at the first two model clips, then drops a window that is too short.
    assert len(kept) == 1
    assert kept[0]["end"] == 45
    assert kept[0]["caption"] == "too long"
