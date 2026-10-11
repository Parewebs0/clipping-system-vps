"""Each graph node with the LLM mocked."""
from __future__ import annotations

from app.shadow.decider_graph import nodes
from app.shadow.decider_graph.state import ShadowRuntime
from tests.shadow.helpers import ASSET_ID, fake_llm, packed_asset, runtime_for

GOOD_CLIP = {
    "start": 0.0,
    "end": 22.0,
    "title": "ForgeGUI shop UI in seconds",
    "caption": "ForgeGUI builds a Roblox shop UI.",
    "on_screen_text": "ForgeGUI",
    "hook_end": 2.0,
    "reason": "clear demo",
}


def _after_extract(packed, runtime=None):
    runtime = runtime or runtime_for(packed)
    st = {"asset_id": packed["asset_id"]}
    st.update(nodes.load_context(st, runtime))
    st.update(nodes.extract_rules(st, runtime))
    return st, runtime


def test_load_context_from_snapshot():
    packed = packed_asset()
    st = nodes.load_context({"asset_id": ASSET_ID}, runtime_for(packed))
    assert st["campaign_id"] == 7
    assert st["segments"]
    assert st["error"] is None
    assert st["confidence_threshold"] == 0.55


def test_extract_rules_reuses_runtime_brief():
    packed = packed_asset(hook=True, terms=["Kanye"])
    st, _ = _after_extract(packed)
    assert st["duration_min"] == 15.0
    assert st["duration_max"] == 45.0
    assert "Hook" in st["brief"]
    assert "Kanye" in st["brief"]
    assert st["ruleset"]["hook"]["required"] is True


def test_propose_clips_uses_injected_llm():
    packed = packed_asset()
    st, rt = _after_extract(packed, runtime_for(packed, llm=fake_llm([GOOD_CLIP])))
    out = nodes.propose_clips(st, rt)
    assert len(out["proposed_clips"]) == 1
    assert out["proposed_clips"][0]["title"].startswith("ForgeGUI")
    assert out["llm_calls"][0]["model"] == "mock"
    assert out["llm_calls"][0]["cost_usd"] == 0.001


def test_propose_clips_heuristic_without_llm():
    packed = packed_asset()
    st, rt = _after_extract(packed)
    out = nodes.propose_clips(st, rt)
    assert out["proposed_clips"]
    assert out["llm_calls"][0]["model"] == "heuristic"


def test_score_clips_duration_hook_and_copy():
    packed = packed_asset(hook=True, terms=["Kanye"])
    st, rt = _after_extract(packed)
    st["proposed_clips"] = [GOOD_CLIP]
    out = nodes.score_clips(st, rt)
    clip = out["scored_clips"][0]
    assert clip["checks"]["duration_ok"] is True
    assert clip["checks"]["hook_ok"] is True
    assert clip["checks"]["copy_ok"] is True
    assert clip["score"] > 0.8
    assert out["confidence"] > 0.8


def test_score_clips_zero_on_prohibited_term():
    packed = packed_asset(terms=["Kanye"])
    st, rt = _after_extract(packed)
    bad = dict(GOOD_CLIP, title="Kanye surprise", caption="featuring Kanye")
    st["proposed_clips"] = [bad]
    out = nodes.score_clips(st, rt)
    assert out["scored_clips"][0]["score"] == 0.0
    assert out["scored_clips"][0]["checks"]["prohibited_terms"] is True


def test_score_clips_flags_missing_hook():
    packed = packed_asset(hook=True)
    st, rt = _after_extract(packed)
    st["proposed_clips"] = [{**GOOD_CLIP, "hook_end": None}]
    out = nodes.score_clips(st, rt)
    assert out["scored_clips"][0]["checks"]["hook_ok"] is False
    assert out["scored_clips"][0]["score"] < 1.0


def test_decide_picks_top_two_or_none():
    packed = packed_asset()
    st, rt = _after_extract(packed)
    good = {**GOOD_CLIP, "checks": {"duration_ok": True, "prohibited_terms": False}, "score": 0.9}
    other = {**GOOD_CLIP, "start": 22, "end": 40, "title": "b",
             "checks": {"duration_ok": True, "prohibited_terms": False}, "score": 0.8}
    weak = {**GOOD_CLIP, "start": 40, "end": 50, "title": "c",
            "checks": {"duration_ok": True, "prohibited_terms": False}, "score": 0.2}
    st["scored_clips"] = [good, other, weak]
    out = nodes.decide(st, rt)
    assert out["decision"] == "clips"
    assert len(out["chosen_clips"]) == 2

    st["scored_clips"] = [{**GOOD_CLIP, "checks": {"duration_ok": False, "prohibited_terms": False}, "score": 0.9}]
    out = nodes.decide(st, rt)
    assert out["decision"] == "none"


def test_report_json_never_needs_db():
    packed = packed_asset()
    st, rt = _after_extract(packed)
    st.update({"chosen_clips": [GOOD_CLIP], "decision": "clips", "decision_reason": "ok",
               "llm_calls": [{"cost_usd": 0.01, "latency_ms": 12}], "would_request_approval": False})
    out = nodes.report(st, rt)
    assert out["report"]["shadow"] is True
    assert out["report"]["llm_cost_usd"] == 0.01
    assert out["report"]["asset_id"] == ASSET_ID
