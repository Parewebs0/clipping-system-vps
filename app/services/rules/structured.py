"""Deterministic mapping of the source's structured fields (no LLM) (#33).

contentrewards detail: configuration.requirement + configuration.rules.
"""
from __future__ import annotations

from app.services.rules.schema import (
    AccountRequirement,
    Evidence,
    ManualCheck,
    RuleSet,
    UnsupportedRule,
)


def _ev(path: str, value) -> Evidence:
    return Evidence(quote=f"{path}={value}", source="structured", verified=True)


def apply_structured(rs: RuleSet, requirement: dict | None, rules: dict | None) -> RuleSet:
    req = requirement or {}
    ru = rules or {}

    def truthy(d, *path):
        cur = d
        for p in path:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(p)
        return cur

    if req.get("brandLogo") or truthy(ru, "branding", "logoRequired"):
        rs.logo.required = True
        rs.logo.evidence.append(_ev("requirement.brandLogo/rules.branding.logoRequired", True))
    vl = req.get("videoLength")
    if isinstance(vl, (int, float)) and vl > 0:
        rs.duration.required = True
        if rs.duration.min_s is None or rs.duration.min_s < vl:
            rs.duration.min_s = float(vl)
        rs.duration.evidence.append(_ev("requirement.videoLength", vl))
    if req.get("linkInBio"):
        url = req.get("linkInBioUrl") or ""
        rs.account_requirements.append(AccountRequirement(
            required=True, kind="link_in_bio", text=f"Link in bio: {url}".strip(),
            evidence=[_ev("requirement.linkInBio", url or True)]))
    if req.get("preApproval"):
        rs.pre_approval.required = True
        rs.pre_approval.evidence.append(_ev("requirement.preApproval", True))
    if req.get("demographics"):
        rs.account_requirements.append(AccountRequirement(
            required=True, kind="audience", text="Audience demographics requirement (see brief)",
            evidence=[_ev("requirement.demographics", True)]))
    bmd = req.get("botMonitoringDays")
    if isinstance(bmd, (int, float)) and bmd > 0:
        rs.account_requirements.append(AccountRequirement(
            required=True, kind="keep_live", text=f"Posts monitored for bots for {int(bmd)} days (keep live, organic views only)",
            evidence=[_ev("requirement.botMonitoringDays", bmd)]))
    if req.get("faceOnCamera") or ru.get("faceOnCamera") is True:
        rs.unsupported.append(UnsupportedRule(
            text="Face on camera required", reason="the pipeline cannot add a creator face",
            evidence=[_ev("requirement.faceOnCamera", True)]))
    if req.get("noRepostedContent") or ru.get("noReposts"):
        rs.edit.required = True
        rs.edit.evidence.append(_ev("requirement.noRepostedContent/rules.noReposts", True))
    if truthy(ru, "sound", "keepOriginalAudio"):
        rs.audio.required = True
        rs.audio.original_only = True
        rs.audio.evidence.append(_ev("rules.sound.keepOriginalAudio", True))
    if truthy(ru, "sound", "mustUseProvided"):
        rs.audio.required = True
        rs.audio.must_use_provided_sound = True
        rs.audio.evidence.append(_ev("rules.sound.mustUseProvided", True))
    if truthy(ru, "source", "mustUseProvided"):
        rs.source.required = True
        rs.source.only_provided = True
        rs.source.evidence.append(_ev("rules.source.mustUseProvided", True))
    if truthy(ru, "content", "noExplicit"):
        rs.prohibitions.required = True
        if "explicit content" not in rs.prohibitions.topics:
            rs.prohibitions.topics.append("explicit content")
        rs.prohibitions.evidence.append(_ev("rules.content.noExplicit", True))
    if truthy(ru, "audio", "clearSpeech"):
        rs.manual_checks.append(ManualCheck(required=True, text="Clear speech audio",
                                            evidence=[_ev("rules.audio.clearSpeech", True)]))
    if truthy(ru, "video", "wellLit"):
        rs.manual_checks.append(ManualCheck(required=True, text="Well-lit video",
                                            evidence=[_ev("rules.video.wellLit", True)]))
    return rs
