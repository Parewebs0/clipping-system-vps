"""Pydantic schemas for Clip."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


VALID_QA_STATUSES = {"pending", "pass", "fail", "review"}
VALID_STATUSES = {"created", "approved", "rejected", "review", "published"}


class ClipCreate(BaseModel):
    campaign_id: int
    asset_id: uuid.UUID
    candidate_id: Optional[uuid.UUID] = None
    render_job_id: Optional[uuid.UUID] = None
    file_path: Optional[str] = Field(None, max_length=1024)
    duration_seconds: Optional[float] = Field(None, ge=0)
    file_size: Optional[int] = Field(None, ge=0)
    qa_status: str = "pending"
    qa_result: dict[str, Any] = Field(default_factory=dict)
    status: str = "created"

    @field_validator("qa_status")
    @classmethod
    def _check_qa_status(cls, v: str) -> str:
        if v not in VALID_QA_STATUSES:
            raise ValueError(f"qa_status must be one of {sorted(VALID_QA_STATUSES)}")
        return v

    @field_validator("status")
    @classmethod
    def _check_status(cls, v: str) -> str:
        if v not in VALID_STATUSES:
            raise ValueError(f"status must be one of {sorted(VALID_STATUSES)}")
        return v


class ClipUpdate(BaseModel):
    qa_status: Optional[str] = None
    qa_result: Optional[dict[str, Any]] = None
    status: Optional[str] = None
    qa_job_id: Optional[uuid.UUID] = None
    file_path: Optional[str] = None
    duration_seconds: Optional[float] = None
    file_size: Optional[int] = None
    published_at: Optional[datetime] = None

    @field_validator("qa_status")
    @classmethod
    def _check_qa_status(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in VALID_QA_STATUSES:
            raise ValueError(f"qa_status must be one of {sorted(VALID_QA_STATUSES)}")
        return v

    @field_validator("status")
    @classmethod
    def _check_status(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in VALID_STATUSES:
            raise ValueError(f"status must be one of {sorted(VALID_STATUSES)}")
        return v


class ClipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    campaign_id: int
    candidate_id: Optional[uuid.UUID] = None
    asset_id: uuid.UUID
    render_job_id: Optional[uuid.UUID] = None
    qa_job_id: Optional[uuid.UUID] = None
    file_path: Optional[str] = None
    duration_seconds: Optional[float] = None
    file_size: Optional[int] = None
    qa_status: str
    qa_result: dict
    status: str
    created_at: datetime
    updated_at: datetime
    qa_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    publish_approved_at: Optional[datetime] = None
    # ── Step 18: per-campaign storage location (set by the Worker on QA pass) ──
    location: Optional[str] = None
    final_path_worker: Optional[str] = None
    location_updated_at: Optional[datetime] = None
    # #43 post-render rules verifier
    compliance_status: str = "pending"
    compliance_report: dict = {}
    # MOLI-55: YouTube views / retention and calibrated clip_score. {} until the tick runs.
    analytics: dict = {}


class ApprovePublishIn(BaseModel):
    platforms: Optional[list[str]] = None
    # Index into compliance_report.checks, or a rule name that matches one review row (#53).
    confirmed_checks: list[int | str] = Field(default_factory=list)


class ClipPublicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    clip_id: uuid.UUID
    platform: str
    status: str
    post_url: Optional[str] = None
    posted_at: Optional[datetime] = None
    job_id: Optional[uuid.UUID] = None
    error_message: Optional[str] = None
    submit_status: str
    submitted_at: Optional[datetime] = None
    submit_ref: Optional[str] = None
    submit_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ApprovePublishOut(BaseModel):
    clip: ClipOut
    already_approved: bool
    publications: list[ClipPublicationOut]


class SocialAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    platform: str
    account_key: str = "default"
    handle: Optional[str] = None
    status: str
    auth_kind: str
    checked_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


SocialPlatformLiteral = Literal["youtube", "instagram", "tiktok", "facebook"]
SocialStatusLiteral = Literal["healthy", "expired", "disabled"]
SocialAuthKindLiteral = Literal["oauth", "browser_profile"]
_ACCOUNT_KEY = r"^[a-z0-9_]{1,64}$"


def _strip_handle(v: Optional[str]) -> Optional[str]:
    if v is None:
        return None
    v = v.strip()
    return v or None


class SocialAccountCreate(BaseModel):
    """POST /social_accounts. Unknown fields (tokens, secrets) are rejected."""

    model_config = ConfigDict(extra="forbid")

    platform: SocialPlatformLiteral
    account_key: str = Field(default="default", pattern=_ACCOUNT_KEY, max_length=64)
    handle: Optional[str] = Field(None, max_length=128)
    status: SocialStatusLiteral = "healthy"
    auth_kind: SocialAuthKindLiteral = "oauth"

    @field_validator("handle")
    @classmethod
    def _handle(cls, v: Optional[str]) -> Optional[str]:
        return _strip_handle(v)


class SocialAccountUpdate(BaseModel):
    """PATCH /social_accounts/{platform}/{account_key}. Identity is the path."""

    model_config = ConfigDict(extra="forbid")

    handle: Optional[str] = Field(None, max_length=128)
    status: Optional[SocialStatusLiteral] = None
    auth_kind: Optional[SocialAuthKindLiteral] = None

    @field_validator("handle")
    @classmethod
    def _handle(cls, v: Optional[str]) -> Optional[str]:
        return _strip_handle(v)

    @model_validator(mode="before")
    @classmethod
    def _enums_not_null(cls, data):
        if isinstance(data, dict):
            for key in ("status", "auth_kind"):
                if key in data and data[key] is None:
                    raise ValueError(f"{key} cannot be null")
        return data
