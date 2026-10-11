"""human_approval interrupt vs shadow continue."""
from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver

from app.shadow.decider_graph.graph import build_graph
from app.shadow.decider_graph.nodes import human_approval
from app.shadow.decider_graph.state import ShadowRuntime
from tests.shadow.helpers import ASSET_ID, fake_llm, packed_asset, runtime_for

CLIP = {
    "start": 0.0,
    "end": 22.0,
    "title": "ForgeGUI shop UI in seconds",
    "caption": "ForgeGUI builds a Roblox shop UI.",
    "on_screen_text": "ForgeGUI",
    "hook_end": 2.0,
    "reason": "demo",
}


def test_shadow_records_would_request_approval(monkeypatch):
    called = []
    monkeypatch.setattr("langgraph.types.interrupt", lambda value: called.append(value))
    packed = packed_asset(rate=8.0, remaining=20_000)
    st = {
        "asset_id": ASSET_ID,
        "campaign_id": 7,
        "campaign": packed["campaign"],
        "chosen_clips": [CLIP],
        "decision": "clips",
        "shadow": True,
    }
    rt = ShadowRuntime(shadow=True, estimated_views=10_000, approval_threshold=50.0)
    out = human_approval(st, rt)
    assert out["would_request_approval"] is True
    assert out["approval_note"] == "habría pedido aprobación"
    assert out["stake_usd"] == 80.0
    assert called == []


def test_interrupt_called_when_not_shadow(monkeypatch):
    called = []

    def fake_interrupt(value):
        called.append(value)
        return {"approved": True}

    monkeypatch.setattr("langgraph.types.interrupt", fake_interrupt)
    packed = packed_asset(rate=8.0)
    st = {
        "asset_id": ASSET_ID,
        "campaign_id": 7,
        "campaign": packed["campaign"],
        "chosen_clips": [CLIP],
        "decision": "clips",
        "shadow": False,
    }
    rt = ShadowRuntime(shadow=False, estimated_views=10_000, approval_threshold=50.0)
    out = human_approval(st, rt)
    assert called
    assert called[0]["kind"] == "human_approval"
    assert called[0]["stake_usd"] == 80.0
    assert out["would_request_approval"] is True


def test_below_threshold_skips_interrupt(monkeypatch):
    called = []
    monkeypatch.setattr("langgraph.types.interrupt", lambda value: called.append(value))
    packed = packed_asset(rate=0.5)
    st = {
        "asset_id": ASSET_ID,
        "campaign": packed["campaign"],
        "chosen_clips": [CLIP],
        "decision": "clips",
        "shadow": False,
    }
    rt = ShadowRuntime(shadow=False, estimated_views=10_000, approval_threshold=50.0)
    out = human_approval(st, rt)
    assert out["would_request_approval"] is False
    assert called == []


def test_graph_shadow_completes_past_approval_gate():
    packed = packed_asset(rate=8.0)
    rt = runtime_for(
        packed,
        llm=fake_llm([CLIP]),
        approval_threshold=1.0,
        estimated_views=10_000,
    )
    graph = build_graph(checkpointer=InMemorySaver())
    result = graph.invoke(
        {"asset_id": ASSET_ID, "shadow": True},
        config={"configurable": {"runtime": rt, "thread_id": "approval"}},
    )
    assert result.get("__interrupt__") in (None, (), [])
    assert result["report"]["would_request_approval"] is True
    assert result["report"]["approval_note"] == "habría pedido aprobación"
