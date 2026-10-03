"""Pydantic schemas for campaigns.

CampaignSpec is provider-agnostic (Step 3 of architecture_flow.md).
Source info (provider, id, url, metadata) tells OpenClaw/MiniMax where
the campaign came from so it can extract rules differently per source.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.campaign import ALLOWED_CAMPAIGN_SOURCES


# Source providers we currently support (whop, manual).
# Must match ck_campaigns_source_provider (migration 0010): anything else would
# pass validation and then fail at INSERT with a 500.
ALLOWED_SOURCES = set(ALLOWED_CAMPAIGN_SOURCES)


class CampaignSpec(BaseModel):
    """Provider-agnostic normalized rules — same shape regardless of source."""
    duration_min: Optional[float] = None
    duration_max: Optional[float] = None
    captions_required: bool = False
    watermark_url: Optional[str] = None
    format: Optional[str] = None  # e.g. "9:16", "1:1", "16:9"
    language: Optional[str] = None
    keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    # Future-proofing for richer specs (campaign-specific rules, asset filters, etc.)
    # Documented keys:
    #   - "qa_rules": dict con reglas técnicas que el QA Worker (FFprobe) aplica:
    #       { "width": int, "height": int, "min_fps": float,
    #         "require_audio": bool, "codec": str }
    #     (poblado por app.campaign_engine.normalizer).
    #   - "qa_rules_source": "local" | "llm"  (trazabilidad del origen).
    #   - "notes": list[str]  (notas del parser si quedaron huecos).
    extra: dict[str, Any] = Field(default_factory=dict)


class CampaignCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=256)
    # source_* are optional but source_provider defaults to 'manual'
    source_provider: str = "manual"
    source_id: Optional[str] = Field(None, max_length=256)
    source_url: Optional[str] = Field(None, max_length=2048)
    source_metadata: dict[str, Any] = Field(default_factory=dict)
    source_instructions: Optional[str] = None
    spec: Optional[CampaignSpec] = None

    @field_validator("source_provider")
    @classmethod
    def _check_source(cls, v: str) -> str:
        if v not in ALLOWED_SOURCES:
            raise ValueError(
                f"source_provider must be one of {sorted(ALLOWED_SOURCES)}"
            )
        return v


class CampaignSpecPatch(BaseModel):
    """Partial spec update: only the fields sent are changed; `extra` is merged
    key by key so the scorer's data (spec.extra.score, score_breakdown, ...)
    survives an edit from the dashboard."""
    model_config = ConfigDict(extra="forbid")

    duration_min: Optional[float] = Field(None, ge=0, le=3600)
    duration_max: Optional[float] = Field(None, ge=0, le=3600)
    captions_required: Optional[bool] = None
    watermark_url: Optional[str] = Field(None, max_length=2048)
    format: Optional[str] = Field(None, max_length=16)
    language: Optional[str] = Field(None, max_length=16)
    keywords: Optional[list[str]] = None
    exclude_keywords: Optional[list[str]] = None
    extra: Optional[dict[str, Any]] = None

    @model_validator(mode="after")
    def _check_durations(self):
        if (
            self.duration_min is not None
            and self.duration_max is not None
            and self.duration_min > self.duration_max
        ):
            raise ValueError("duration_min must be <= duration_max")
        return self


class CampaignUpdate(BaseModel):
    """PATCH /campaigns/{id}. Every field optional; unknown fields -> 422.

    * `status` goes through the manual state machine
      (app/services/campaign_transitions.py): unknown -> 400, not allowed -> 409.
    * `source_url` / `source_id` are only editable on `manual` campaigns:
      for `whop` campaigns source_url is the discovery dedup key.
    * `source_provider` is never editable (identity of the origin).
    * `source_metadata` is merged on top of the existing dict.
    * Explicit `null` clears nullable text fields (source_url, source_id,
      source_instructions).
    """
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(None, min_length=1, max_length=256)
    source_url: Optional[str] = Field(None, max_length=2048)
    source_id: Optional[str] = Field(None, max_length=256)
    source_instructions: Optional[str] = Field(None, max_length=20000)
    status: Optional[str] = None
    status_reason: Optional[str] = Field(None, max_length=500)
    spec: Optional[CampaignSpecPatch] = None
    source_metadata: Optional[dict[str, Any]] = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank")
        return v

    @field_validator("source_url")
    @classmethod
    def _check_url(cls, v: Optional[str]) -> Optional[str]:
        if v and not v.startswith(("http://", "https://")):
            raise ValueError("source_url must start with http:// or https://")
        return v


class CampaignStatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str = Field(..., min_length=1, max_length=32)
    reason: Optional[str] = Field(None, max_length=500)


class StatusMachineState(BaseModel):
    value: str
    consumed_by: Optional[str] = None
    effect: Optional[str] = None
    manual_targets: list[str]


class StatusMachineOut(BaseModel):
    statuses: list[StatusMachineState]
    transitions: dict[str, list[str]]


class CampaignOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    status: str
    source_provider: str
    source_id: Optional[str] = None
    source_url: Optional[str] = None
    source_metadata: dict
    source_instructions: Optional[str] = None
    spec: dict
    assets_count: int
    clips_approved: int
    clips_published: int
    created_at: datetime
    updated_at: datetime
