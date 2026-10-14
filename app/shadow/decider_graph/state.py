"""Graph state and the injectable runtime (LLM, read-only DB, thresholds)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, TypedDict

from pydantic import BaseModel, ConfigDict, Field


class ProposedClip(BaseModel):
    """Structured LLM clip proposal."""

    model_config = ConfigDict(extra="ignore")
    start: float
    end: float
    title: str = ""
    caption: str = ""
    on_screen_text: str = ""
    hook_end: float | None = None
    reason: str = ""
    hook: float | None = None
    flow: float | None = None
    value: float | None = None
    fit: float | None = None
    score: float | None = None


class ProposeClipsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    clips: list[ProposedClip] = Field(default_factory=list)


LlmFn = Callable[..., tuple[dict[str, Any], dict[str, Any]]]


@dataclass
class ShadowRuntime:
    """Injected into graph config. Tests pass fakes; the CLI passes fixtures or a read-only session."""

    db: Any = None
    llm: LlmFn | None = None
    shadow: bool = True
    snapshots: dict[str, Any] | None = None
    similar_examples: list[dict[str, Any]] | None = None
    estimated_views: float = 10_000.0
    confidence_threshold: float = 0.55
    approval_threshold: float = 50.0
    llm_calls: list[dict[str, Any]] = field(default_factory=list)


class DeciderState(TypedDict, total=False):
    asset_id: str
    campaign_id: int
    shadow: bool
    asset: dict[str, Any]
    campaign: dict[str, Any]
    segments: list[dict[str, Any]]
    transcript_text: str
    video_duration: float
    ruleset: dict[str, Any]
    duration_min: float
    duration_max: float
    brief: str
    decider_options: dict[str, Any]
    proposed_clips: list[dict[str, Any]]
    scored_clips: list[dict[str, Any]]
    similar_examples: list[dict[str, Any]]
    context_iteration: int
    confidence: float
    confidence_threshold: float
    chosen_clips: list[dict[str, Any]]
    decision: str
    decision_reason: str
    would_request_approval: bool
    approval_note: str | None
    approval_payload: dict[str, Any] | None
    stake_usd: float
    llm_calls: list[dict[str, Any]]
    report: dict[str, Any]
    error: str | None
