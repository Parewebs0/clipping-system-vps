"""Runtime view of the campaign rules (#35) — single source of truth.

Every pipeline step (decider, candidate approval, QA, render, copy) reads the
campaign rules through these helpers. Order: source_metadata.ruleset (v2) →
campaign.spec (manual campaigns / legacy) → provider defaults. The old regex
parser over source_instructions is no longer used.
"""
from __future__ import annotations

from typing import Any, Optional

from app.campaign_engine.models import CampaignHints, NormalizedSpec
from app.campaign_engine.normalizer import _default_qa_rules, normalize
from app.services.rules.schema import RuleSet, load_ruleset


def ruleset(campaign) -> Optional[RuleSet]:
    if campaign is None:
        return None
    return load_ruleset(campaign.source_metadata or {})


def _f(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def effective_spec(campaign) -> NormalizedSpec:
    provider = getattr(campaign, "source_provider", None) or "other"
    rs = ruleset(campaign)
    if rs is not None:
        fmt = rs.aspect.ratio or "9:16"
        return NormalizedSpec(
            duration_min=float(rs.duration.min_s if rs.duration.min_s is not None else 15.0),
            duration_max=float(rs.duration.max_s if rs.duration.max_s is not None else 45.0),
            captions_required=bool(rs.captions.required),
            watermark_url=rs.logo.url if rs.logo.required else None,
            format=fmt,
            language=rs.language.code,
            keywords=[],
            exclude_keywords=list(rs.prohibitions.terms),
            source_provider=provider,
            extra={"qa_rules": _default_qa_rules(fmt), "ruleset_version": rs.version},
        )
    base = normalize(CampaignHints(), provider)
    spec = dict(getattr(campaign, "spec", None) or {})
    dmin = _f(spec.get("duration_min"))
    dmax = _f(spec.get("duration_max"))
    fmt = spec.get("format") or base.format
    dmin = base.duration_min if dmin is None else dmin
    dmax = base.duration_max if dmax is None else dmax
    if dmax <= dmin:
        dmax = dmin + 10.0
    extra = dict(spec.get("extra") or {})
    return NormalizedSpec(
        duration_min=dmin,
        duration_max=dmax,
        captions_required=bool(spec.get("captions_required", base.captions_required)),
        watermark_url=spec.get("watermark_url"),
        format=fmt,
        language=spec.get("language") or base.language,
        keywords=list(spec.get("keywords") or []),
        exclude_keywords=list(spec.get("exclude_keywords") or []),
        source_provider=provider,
        extra={"qa_rules": extra.get("qa_rules") or _default_qa_rules(fmt)},
    )


def duration_window(campaign) -> tuple[float, float]:
    s = effective_spec(campaign)
    return float(s.duration_min), float(s.duration_max)


def qa_rules(campaign) -> dict:
    s = effective_spec(campaign)
    rules = dict(_default_qa_rules(s.format))
    rules.update({k: v for k, v in (s.extra.get("qa_rules") or {}).items() if v is not None})
    rules["min_duration"] = float(s.duration_min)
    rules["max_duration"] = float(s.duration_max)
    return rules


def segment_text(asset, start: float, end: float) -> str:
    """Transcript text overlapping [start, end] (falls back to full text)."""
    tx = (getattr(asset, "extra_metadata", None) or {}).get("transcription") or {}
    segs = tx.get("segments") or []
    parts = []
    for s in segs:
        if not isinstance(s, dict):
            continue
        a, b = _f(s.get("start")) or 0.0, _f(s.get("end")) or 0.0
        if b > start and a < end:
            parts.append(str(s.get("text") or "").strip())
    if parts:
        return " ".join(parts)
    return "" if segs else str(tx.get("text") or "")


def decider_brief(campaign) -> str:
    """Compact rule summary for the clip decider prompt."""
    rs = ruleset(campaign)
    if rs is None:
        return ""
    lines = []
    if rs.hook.required:
        lines.append(f"- Hook: the first {rs.hook.max_seconds or 3:g}s must be a strong, self-contained hook (no slow intro).")
    if rs.language.code:
        lines.append(f"- Language: {rs.language.code} only.")
    if rs.prohibitions.terms:
        lines.append("- Never pick windows mentioning: " + ", ".join(rs.prohibitions.terms[:20]))
    for t in rs.prohibitions.topics[:10]:
        lines.append(f"- Avoid: {t}")
    for m in rs.manual_checks[:8]:
        lines.append(f"- {m.text}")
    if rs.on_screen_text.required or rs.on_screen_text.must_include:
        inc = ", ".join(rs.on_screen_text.must_include) or "(free wording)"
        lines.append(f"- Propose on_screen_text (max 60 chars, accurate to the clip) containing: {inc}.")
        if rs.on_screen_text.guidance:
            lines.append(f"  guidance: {rs.on_screen_text.guidance[:200]}")
    if rs.copy_rules.must_mention_any:
        lines.append("- Title/caption must mention one of: " + ", ".join(rs.copy_rules.must_mention_any))
    return "\n".join(lines)
