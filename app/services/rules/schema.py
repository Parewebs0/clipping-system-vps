"""RuleSet v2 — typed, versioned campaign rules (#33).

Every rule carries:
  * `required`     — the campaign demands it;
  * `evidence`     — literal quotes from the source (detail / guidelines / doc);
  * `enforcement`  — who guarantees it:
        auto        the pipeline applies AND verifies it;
        human       a person must confirm it (scope=campaign: once per campaign,
                    e.g. account requirements; scope=clip: at clip review);
        unsupported the pipeline cannot do it → the campaign must not be worked.
Enforcement is assigned deterministically (enforcement.py), never by the LLM.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

RULESET_VERSION = 2

Enforcement = Literal["auto", "human", "unsupported"]
Scope = Literal["campaign", "clip"]
SourceKind = Literal["structured", "guidelines", "doc", "description", "default", "llm"]


class Evidence(BaseModel):
    model_config = ConfigDict(extra="ignore")
    quote: str
    source: SourceKind = "llm"
    verified: Optional[bool] = None  # quote found verbatim (normalised) in the source text


class Rule(BaseModel):
    model_config = ConfigDict(extra="ignore")
    required: bool = False
    evidence: list[Evidence] = Field(default_factory=list)
    enforcement: Enforcement = "auto"
    scope: Scope = "clip"
    note: Optional[str] = None


class DurationRule(Rule):
    min_s: Optional[float] = None
    max_s: Optional[float] = None


class AspectRule(Rule):
    ratio: str = "9:16"


class LanguageRule(Rule):
    code: Optional[str] = None  # ISO 639-1


class CaptionsRule(Rule):
    """Burned-in subtitles."""
    brand_dictionary: list[str] = Field(default_factory=list)  # exact spellings (e.g. "BOXABL")


class OnScreenTextRule(Rule):
    must_include: list[str] = Field(default_factory=list)
    guidance: Optional[str] = None


class LogoRule(Rule):
    url: Optional[str] = None
    position: Literal["top_left", "top_right", "bottom_left", "bottom_right", "center"] = "top_right"
    timing: Literal["full", "start", "end"] = "full"
    min_seconds: Optional[float] = None
    as_cta: bool = False  # logo shown inside a call-to-action (e.g. end card)
    cta_text: Optional[str] = None


class AudioRule(Rule):
    original_only: bool = False
    no_added_music: bool = False
    must_use_provided_sound: bool = False
    music_recommended: bool = False


class HookRule(Rule):
    max_seconds: Optional[float] = None


class EditRule(Rule):
    """'Do not post raw rips' — footage must be edited."""
    allowed_edits: list[str] = Field(default_factory=list)


class SourceRule(Rule):
    only_provided: bool = False
    allowed_urls: list[str] = Field(default_factory=list)


class HashtagRule(Rule):
    ordered: list[str] = Field(default_factory=list)
    position: Literal["after_text", "anywhere"] = "anywhere"
    max_extra: Optional[int] = None


class FTCRule(Rule):
    tokens: list[str] = Field(default_factory=list)  # any one of these
    own_line: bool = False
    first_after_text: bool = False


class CopyRule(Rule):
    exact_caption: Optional[str] = None
    must_mention_any: list[str] = Field(default_factory=list)
    mentions_by_platform: dict[str, list[str]] = Field(default_factory=dict)
    hashtags: HashtagRule = Field(default_factory=HashtagRule)
    ftc: FTCRule = Field(default_factory=FTCRule)
    pinned_comment: Optional[str] = None


class ProhibitionRule(Rule):
    terms: list[str] = Field(default_factory=list)   # literal words/phrases → auto text check
    topics: list[str] = Field(default_factory=list)  # semantic → human (clip review)


class AccountRequirement(Rule):
    kind: str = "other"  # bio | link_in_bio | audience | engagement | warmup | posting_limit | keep_live | community | likes_public | other
    text: str = ""


class ManualCheck(Rule):
    """Content/quality rule a reviewer must confirm on each clip (human, scope=clip)."""
    text: str = ""


class UnsupportedRule(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str
    reason: str = ""
    evidence: list[Evidence] = Field(default_factory=list)
    blocking: bool = True


class Coverage(BaseModel):
    normative_lines: int = 0
    covered_first_pass: int = 0
    mapped_second_pass: int = 0
    unsupported_second_pass: int = 0
    ignored: int = 0
    uncovered: list[str] = Field(default_factory=list)
    evidence_total: int = 0
    evidence_verified: int = 0


class RuleSet(BaseModel):
    model_config = ConfigDict(extra="ignore")
    version: int = RULESET_VERSION
    platforms: list[str] = Field(default_factory=list)
    duration: DurationRule = Field(default_factory=DurationRule)
    aspect: AspectRule = Field(default_factory=lambda: AspectRule(required=True))
    language: LanguageRule = Field(default_factory=LanguageRule)
    captions: CaptionsRule = Field(default_factory=CaptionsRule)
    on_screen_text: OnScreenTextRule = Field(default_factory=OnScreenTextRule)
    logo: LogoRule = Field(default_factory=LogoRule)
    audio: AudioRule = Field(default_factory=AudioRule)
    hook: HookRule = Field(default_factory=HookRule)
    edit: EditRule = Field(default_factory=EditRule)
    source: SourceRule = Field(default_factory=SourceRule)
    copy_rules: CopyRule = Field(default_factory=CopyRule, alias="copy")
    prohibitions: ProhibitionRule = Field(default_factory=ProhibitionRule)
    account_requirements: list[AccountRequirement] = Field(default_factory=list)
    manual_checks: list[ManualCheck] = Field(default_factory=list)
    pre_approval: Rule = Field(default_factory=Rule)
    unsupported: list[UnsupportedRule] = Field(default_factory=list)
    coverage: Coverage = Field(default_factory=Coverage)
    content_source_urls: list[str] = Field(default_factory=list)
    meta: dict = Field(default_factory=dict)  # extracted_at, model, cost, sources...

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    def dump(self) -> dict:
        return self.model_dump(mode="json", by_alias=True)


def load_ruleset(meta: dict | None) -> Optional[RuleSet]:
    raw = (meta or {}).get("ruleset")
    if not isinstance(raw, dict):
        return None
    try:
        return RuleSet.model_validate(raw)
    except Exception:  # noqa: BLE001
        return None
