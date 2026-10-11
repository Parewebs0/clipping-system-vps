"""Shared snapshots for shadow graph tests."""
from __future__ import annotations

from uuid import UUID

from app.services.rules.schema import RuleSet
from app.shadow.decider_graph.state import ShadowRuntime


ASSET_ID = str(UUID(int=1))


def _rs(**kwargs) -> dict:
    rs = RuleSet()
    dur = kwargs.get("duration") or (15.0, 45.0)
    rs.duration.min_s, rs.duration.max_s = dur
    if kwargs.get("hook"):
        rs.hook.required = True
        rs.hook.max_seconds = kwargs.get("hook_max", 3.0)
    if kwargs.get("terms"):
        rs.prohibitions.terms = list(kwargs["terms"])
    if kwargs.get("must_mention"):
        rs.copy_rules.must_mention_any = list(kwargs["must_mention"])
    return {"ruleset": rs.dump(), **kwargs.get("econ", {})}


def packed_asset(
    *,
    terms=None,
    hook=False,
    rate=0.5,
    remaining=1000.0,
    duration=90.0,
    extra_segment=None,
) -> dict:
    segs = [
        {"start": 0.0, "end": 4.0, "text": "Watch this ForgeGUI trick for Roblox developers."},
        {"start": 4.0, "end": 12.0, "text": "You can generate a full shop UI in seconds with ForgeGUI."},
        {"start": 12.0, "end": 22.0, "text": "Here is the before and after of a messy frame."},
        {"start": 22.0, "end": 35.0, "text": "Subscribe if you build Roblox games and want faster GUIs."},
    ]
    if extra_segment:
        segs.append(extra_segment)
    text = " ".join(s["text"] for s in segs)
    campaign = {
        "id": 7,
        "name": "test-campaign",
        "status": "scored",
        "source_provider": "whop",
        "source_instructions": None,
        "source_metadata": _rs(
            terms=terms,
            hook=hook,
            econ={
                "economics": {
                    "platform_rates": {"youtube": {"rate_usd": rate}},
                    "remaining_usd": remaining,
                }
            },
        ),
        "spec": {},
    }
    asset = {
        "id": ASSET_ID,
        "campaign_id": 7,
        "duration_seconds": duration,
        "status": "transcribed",
        "extra_metadata": {"transcription": {"text": text, "segments": segs}},
    }
    return {"asset_id": ASSET_ID, "asset": asset, "campaign": campaign}


def runtime_for(packed: dict, **kwargs) -> ShadowRuntime:
    return ShadowRuntime(
        snapshots={packed["asset_id"]: packed, "campaign": packed["campaign"]},
        shadow=True,
        **kwargs,
    )


def fake_llm(clips: list[dict]):
    def _llm(prompt: str, schema=None):
        return {"clips": clips}, {"model": "mock", "cost_usd": 0.001, "latency_ms": 3}

    return _llm
