"""Conditional edge + full graph invoke."""
from __future__ import annotations

from app.shadow.decider_graph.graph import build_graph, run_decider
from app.shadow.decider_graph.nodes import route_after_score
from tests.shadow.helpers import ASSET_ID, fake_llm, packed_asset, runtime_for

CLIP = {
    "start": 0.0,
    "end": 22.0,
    "title": "ForgeGUI shop UI in seconds",
    "caption": "ForgeGUI builds a Roblox shop UI.",
    "on_screen_text": "ForgeGUI",
    "hook_end": 2.0,
    "reason": "clear demo",
}


def test_route_after_score_fetches_once():
    assert route_after_score({"confidence": 0.2, "proposed_clips": [CLIP], "context_iteration": 0}) == "need_more_context"
    assert route_after_score({"confidence": 0.2, "proposed_clips": [CLIP], "context_iteration": 1}) == "decide"
    assert route_after_score({"confidence": 0.9, "proposed_clips": [CLIP], "context_iteration": 0}) == "decide"
    assert route_after_score({"confidence": 0.0, "proposed_clips": [], "context_iteration": 0}) == "decide"


def test_graph_takes_need_more_context_then_decides():
    packed = packed_asset()
    similar = [{"start": 0.0, "end": 20.0, "title": "peer", "score": 0.8, "campaign_id": 99}]
    rt = runtime_for(
        packed,
        llm=fake_llm([CLIP]),
        confidence_threshold=1.1,
        similar_examples=similar,
        approval_threshold=10_000,
    )
    graph = build_graph()
    result = graph.invoke(
        {"asset_id": ASSET_ID, "shadow": True},
        config={"configurable": {"runtime": rt, "thread_id": "edge"}},
    )
    assert result["context_iteration"] == 1
    assert result["similar_examples"] == similar
    assert result["report"]["decision"] in {"clips", "none"}


def test_run_decider_happy_path_picks_clips():
    packed = packed_asset()
    rt = runtime_for(packed, llm=fake_llm([CLIP]), approval_threshold=10_000)
    result = run_decider(ASSET_ID, rt)
    assert result["report"]["decision"] == "clips"
    assert 1 <= len(result["report"]["chosen_clips"]) <= 8
    assert result["report"]["shadow"] is True
