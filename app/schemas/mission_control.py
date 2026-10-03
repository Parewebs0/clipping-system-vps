"""Response models for the read-only Mission Control endpoints (issue #7).

These give the dashboard a typed contract: FastAPI publishes them in the
OpenAPI document and the frontend generates its TypeScript types from it
(`frontend/openapi.json`, see scripts/export_openapi.py).

Status fields use ``Literal`` built from the model enums, which mirror the
DB check constraints, so the generated TS types are exact unions.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from app.models.asset import ASSET_STATUS_VALUES
from app.models.candidate import CANDIDATE_STATUS_VALUES
from app.models.campaign import ALLOWED_CAMPAIGN_SOURCES, CAMPAIGN_STATUS_VALUES
from app.models.clip import CLIP_QA_STATUS_VALUES, CLIP_STATUS_VALUES
from app.models.job import JOB_STATUS_VALUES

CampaignStatusT = Literal[CAMPAIGN_STATUS_VALUES]  # type: ignore[valid-type]
CampaignSourceT = Literal[ALLOWED_CAMPAIGN_SOURCES]  # type: ignore[valid-type]
AssetStatusT = Literal[ASSET_STATUS_VALUES]  # type: ignore[valid-type]
ClipQAStatusT = Literal[CLIP_QA_STATUS_VALUES]  # type: ignore[valid-type]
ClipStatusT = Literal[CLIP_STATUS_VALUES]  # type: ignore[valid-type]
JobStatusT = Literal[JOB_STATUS_VALUES]  # type: ignore[valid-type]
CandidateStatusT = Literal[CANDIDATE_STATUS_VALUES]  # type: ignore[valid-type]


class PipelineError(BaseModel):
    """briefing_error / resolve_error persisted by ticks 3a / 3b.

    kind (3a): no_materials | llm_timeout | other
    kind (3b): gog | social_only | unsupported_source | no_videos
    legacy rows: legacy_string
    """
    kind: Optional[str] = None
    message: Optional[str] = None
    at: Optional[str] = None


class LlmUsageStage(BaseModel):
    stage: str
    calls: int
    errors: int
    total_tokens: int
    cost_usd: float


class LlmUsageSummary(BaseModel):
    calls: int = 0
    errors: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    by_stage: List[LlmUsageStage] = Field(default_factory=list)
    last_call_at: Optional[str] = None


class RecentError(BaseModel):
    id: str
    job_type: str
    error_message: Optional[str] = None
    created_at: Optional[str] = None


class OverviewOut(BaseModel):
    generated_at: str
    total_campaigns: int
    total_jobs_last_24h: int
    total_clips_last_24h: int
    disk_unavailable_videos: int
    campaigns_by_status: Dict[str, int]
    jobs_by_type_status: Dict[str, Dict[str, int]]
    assets_by_status: Dict[str, int]
    clips_by_qa_status: Dict[str, int]
    clips_by_status: Dict[str, int]
    recent_errors: List[RecentError]
    llm_usage_24h: LlmUsageSummary
    llm_usage_total: LlmUsageSummary
    worker_file_base_url: Optional[str] = None


class CampaignListItem(BaseModel):
    id: int
    name: str
    status: CampaignStatusT
    source_provider: CampaignSourceT
    source_url: Optional[str] = None
    source_id: Optional[str] = None
    source_metadata: Dict[str, Any]
    spec: Dict[str, Any]
    assets_count: int
    assets_total: int
    assets_transcribed: int
    clips_total: int
    clips_approved: int
    clips_approved_qa: int
    clips_published: int
    priority_score: Optional[float] = None
    priority_tier: Optional[float] = None
    priority_tie_break: Optional[Any] = None
    priority_breakdown: Optional[Dict[str, Any]] = None
    priority_rank_reason: Optional[str] = None
    score_eligible: Optional[bool] = None
    score_min_to_run: Optional[float] = None
    briefing_error: Optional[PipelineError] = None
    resolve_error: Optional[PipelineError] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class CampaignListOut(BaseModel):
    items: List[CampaignListItem]
    count: int


class CampaignDetailCampaign(BaseModel):
    id: int
    name: str
    status: CampaignStatusT
    source_provider: CampaignSourceT
    source_url: Optional[str] = None
    source_id: Optional[str] = None
    source_metadata: Dict[str, Any]
    source_instructions: Optional[str] = None
    spec: Dict[str, Any]
    assets_count: int
    clips_approved: int
    clips_published: int
    briefing_error: Optional[PipelineError] = None
    resolve_error: Optional[PipelineError] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class McAssetOut(BaseModel):
    id: str
    source_url: str
    source_provider: Optional[str] = None
    asset_type: str
    status: AssetStatusT
    local_path: Optional[str] = None
    file_size: Optional[int] = None
    duration_seconds: Optional[float] = None
    sha256: Optional[str] = None
    mime_type: Optional[str] = None
    extra_metadata: Dict[str, Any] = Field(default_factory=dict)
    downloaded_at: Optional[str] = None
    transcribed_at: Optional[str] = None
    created_at: Optional[str] = None


class McJobOut(BaseModel):
    id: str
    job_type: str
    status: JobStatusT
    priority: int
    attempts: int
    max_attempts: int
    worker_id: Optional[str] = None
    error_message: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    result: Optional[Dict[str, Any]] = None


class McClipOut(BaseModel):
    id: str
    asset_id: Optional[str] = None
    file_path: Optional[str] = None
    duration_seconds: Optional[float] = None
    file_size: Optional[int] = None
    qa_status: ClipQAStatusT
    qa_result: Dict[str, Any] = Field(default_factory=dict)
    status: ClipStatusT
    location: Optional[str] = None
    final_path_worker: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    qa_at: Optional[str] = None
    published_at: Optional[str] = None
    publish_approved_at: Optional[str] = None
    compliance_status: str = "pending"
    compliance_report: Dict[str, Any] = Field(default_factory=dict)


class CampaignDetailOut(BaseModel):
    campaign: CampaignDetailCampaign
    assets: List[McAssetOut]
    active_jobs: List[McJobOut]
    clips: List[McClipOut]
    llm_usage: LlmUsageSummary
    worker_file_base_url: Optional[str] = None


class DiscoveredSummary(BaseModel):
    name: Optional[str] = None
    external_id: Optional[str] = None
    detail_url: Optional[str] = None
    cpm_usd_per_1k: Optional[float] = None
    prize_pool_usd: Optional[float] = None
    joined: Optional[Any] = None


class CampaignRulesOut(BaseModel):
    campaign_id: int
    campaign_name: str
    status: CampaignStatusT
    source_provider: CampaignSourceT
    source_url: Optional[str] = None
    spec: Dict[str, Any]
    spec_is_empty: bool
    rules: Dict[str, Any]
    card_text: str
    discovered: DiscoveredSummary
    asset_links_brief: List[Any]
    asset_links_raw: List[str]
    asset_links_count: int
    drive_ids: List[str]
    brief_docs: List[Any] = Field(default_factory=list)
    score: Optional[Dict[str, Any]] = None
    score_preview: Optional[Dict[str, Any]] = None
    priority_tier: Optional[float] = None
    priority_score: Optional[float] = None
    priority_components: Dict[str, Any]
    briefed_at: Optional[str] = None
    joined: Optional[Any] = None
    cpm_usd_per_1k: Optional[float] = None
    prize_pool_usd: Optional[float] = None


class JobRecentItem(McJobOut):
    campaign_name: Optional[str] = None
    elapsed_seconds: float = 0.0


class JobRecentOut(BaseModel):
    items: List[JobRecentItem]
    count: int


class PipelineStage(BaseModel):
    job_id: str
    status: JobStatusT
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error_message: Optional[str] = None


class PipelineAsset(BaseModel):
    asset_id: str
    source_url: str
    asset_status: AssetStatusT
    stages: Dict[str, PipelineStage]


class PipelineOut(BaseModel):
    campaign_id: int
    assets: List[PipelineAsset]


class VideoItem(BaseModel):
    id: str
    campaign_id: int
    campaign_name: Optional[str] = None
    source_url: str
    local_path: Optional[str] = None
    file_size: Optional[int] = None
    duration_seconds: Optional[float] = None
    status: AssetStatusT
    downloaded_at: Optional[str] = None
    transcribed_at: Optional[str] = None


class VideosOut(BaseModel):
    items: List[VideoItem]
    count: int
    worker_file_base_url: Optional[str] = None


class ClipInventoryItem(McClipOut):
    campaign_id: int
    campaign_name: Optional[str] = None
    asset_source_url: Optional[str] = None
    candidate_id: Optional[str] = None
    render_job_id: Optional[str] = None
    qa_job_id: Optional[str] = None


class ClipsOut(BaseModel):
    items: List[ClipInventoryItem]
    count: int
    worker_file_base_url: Optional[str] = None


# --- Candidates (issue #17) -------------------------------------------------

class TranscriptLine(BaseModel):
    start: float
    end: float
    text: str


class CandidateItem(BaseModel):
    id: str
    campaign_id: int
    campaign_name: Optional[str] = None
    asset_id: str
    asset_source_url: Optional[str] = None
    asset_title: Optional[str] = None
    asset_duration_seconds: Optional[float] = None
    asset_status: Optional[AssetStatusT] = None
    start_time: float
    end_time: float
    duration_seconds: float
    score: Optional[float] = None
    reasoning: Optional[str] = None
    title: Optional[str] = None
    caption: Optional[str] = None
    source: Optional[str] = None
    kind: Optional[str] = None
    status: CandidateStatusT
    approved_at: Optional[str] = None
    rejected_at: Optional[str] = None
    rejected_reason: Optional[str] = None
    render_job_id: Optional[str] = None
    render_job_status: Optional[JobStatusT] = None
    clip_id: Optional[str] = None
    transcript_excerpt: List[TranscriptLine] = Field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class CandidatesOut(BaseModel):
    items: List[CandidateItem]
    count: int
    counts_by_status: Dict[str, int]
