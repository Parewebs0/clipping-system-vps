"""Deterministic enforcement + gate helpers + legacy view (#33).

Never decided by the LLM: the mapping rule → (auto | human | unsupported)
lives here and is unit-tested.
"""
from __future__ import annotations

import hashlib
import re

from app.services.rules.schema import Evidence, RuleSet, UnsupportedRule

DEFAULT_MIN_S = 15.0
DEFAULT_MAX_S = 45.0
PLATFORM_MAX_S = {"youtube": 180.0}

# Hosts the asset resolver can ingest (see asset_resolve.classify).
_UNSUPPORTED_SOURCE_HOSTS = ("f.io", "frame.io", "mediasilo", "we.tl", "wetransfer", "mega.nz")
_IMAGE_HINT = re.compile(r"\.(png|jpe?g|webp|svg)(\?|$)|drive\.google\.com/(file/d|uc\?)", re.I)


def _key(kind: str, text: str) -> str:
    return f"{kind}:{hashlib.sha1(text.strip().lower().encode()).hexdigest()[:10]}"


def assign_enforcement(rs: RuleSet) -> RuleSet:
    # Duration: defaults when the brief is silent.
    d = rs.duration
    if d.min_s is None and d.max_s is None:
        d.min_s, d.max_s = DEFAULT_MIN_S, DEFAULT_MAX_S
        d.evidence.append(Evidence(quote=f"default {DEFAULT_MIN_S:g}-{DEFAULT_MAX_S:g}s (brief silent)", source="default", verified=True))
    elif d.max_s is None:
        d.max_s = max(DEFAULT_MAX_S, (d.min_s or 0) + 15)
        d.note = "max not stated: pipeline default"
    elif d.min_s is None:
        d.min_s = min(DEFAULT_MIN_S, d.max_s - 5)
    d.enforcement, d.scope = "auto", "clip"
    rs.aspect.enforcement, rs.aspect.scope = "auto", "clip"
    if not rs.aspect.ratio:
        rs.aspect.ratio = "9:16"
    rs.language.enforcement = "auto"
    rs.captions.enforcement = "auto"
    rs.on_screen_text.enforcement = "auto"
    if rs.on_screen_text.must_include:
        rs.on_screen_text.required = True

    lg = rs.logo
    if lg.required:
        if lg.url and _IMAGE_HINT.search(lg.url):
            lg.enforcement = "auto"
        else:
            lg.enforcement, lg.scope = "human", "campaign"
            lg.note = "logo required but no logo file URL: provide the file or confirm how it is met"
    else:
        lg.enforcement = "auto"

    au = rs.audio
    au.enforcement = "auto"  # the pipeline never adds music; original audio is kept
    if au.must_use_provided_sound:
        au.enforcement = "unsupported"
        _add_unsupported(rs, "Must use the provided sound", "the pipeline cannot attach platform sounds", au.evidence)

    for r in (rs.hook, rs.edit):
        r.enforcement, r.scope = ("human", "clip") if r.required else ("auto", "clip")

    src = rs.source
    src.enforcement = "auto"
    if src.only_provided and src.allowed_urls:
        bad = [u for u in src.allowed_urls if any(h in u.lower() for h in _UNSUPPORTED_SOURCE_HOSTS)]
        if bad and len(bad) == len(src.allowed_urls):
            src.enforcement = "unsupported"
            _add_unsupported(rs, f"Only official footage, hosted on unsupported host ({bad[0][:80]})",
                             "asset resolver cannot ingest this host", src.evidence)

    cp = rs.copy_rules
    cp.enforcement = "auto"
    cp.hashtags.enforcement = "auto"
    cp.ftc.enforcement = "auto"
    if cp.ftc.tokens:
        cp.ftc.required = True
    if cp.hashtags.ordered:
        cp.hashtags.required = True
    if cp.exact_caption or cp.must_mention_any or any(cp.mentions_by_platform.values()):
        cp.required = True

    pr = rs.prohibitions
    pr.enforcement = "auto" if not pr.topics else "human"
    pr.scope = "clip"
    if pr.terms or pr.topics:
        pr.required = True

    for a in rs.account_requirements:
        a.enforcement, a.scope, a.required = "human", "campaign", True
    for m in rs.manual_checks:
        m.enforcement, m.scope = "human", "clip"
    pa = rs.pre_approval
    pa.enforcement, pa.scope = ("human", "campaign") if pa.required else ("auto", "campaign")
    return rs


def _add_unsupported(rs: RuleSet, text: str, reason: str, evidence) -> None:
    if any(u.text == text for u in rs.unsupported):
        return
    rs.unsupported.append(UnsupportedRule(text=text, reason=reason, evidence=list(evidence or [])))


def blocking_items(rs: RuleSet) -> list[dict]:
    """Items that keep a campaign out of the automatic flow until resolved.

    kind=unsupported → cannot be worked (a human may only *waive* it
    explicitly); kind=human → needs a one-off human confirmation.
    """
    out: list[dict] = []
    for u in rs.unsupported:
        if u.blocking:
            out.append({"key": _key("unsupported", u.text), "kind": "unsupported", "text": u.text,
                        "reason": u.reason, "evidence": [e.quote for e in u.evidence][:2]})
    for a in rs.account_requirements:
        out.append({"key": _key("account", a.text), "kind": "human", "rule": f"account:{a.kind}",
                    "text": a.text, "evidence": [e.quote for e in a.evidence][:2]})
    if rs.pre_approval.required:
        out.append({"key": "human:pre_approval", "kind": "human", "rule": "pre_approval",
                    "text": "Campaign requires pre-approval of posts/creators",
                    "evidence": [e.quote for e in rs.pre_approval.evidence][:2]})
    if rs.logo.required and rs.logo.enforcement == "human":
        out.append({"key": "human:logo_file", "kind": "human", "rule": "logo",
                    "text": rs.logo.note or "logo required", "evidence": [e.quote for e in rs.logo.evidence][:2]})
    return out


def pending_blockers(rs: RuleSet, confirmations: dict | None) -> list[dict]:
    conf = confirmations or {}
    return [b for b in blocking_items(rs) if b["key"] not in conf]


def legacy_rules(rs: RuleSet) -> dict:
    """Old `source_metadata.rules` shape, derived from the RuleSet (compat)."""
    cp = rs.copy_rules
    tags: list[str] = []
    for lst in cp.mentions_by_platform.values():
        for t in lst:
            if t not in tags:
                tags.append(t)
    return {
        "duration_min": rs.duration.min_s,
        "duration_max": rs.duration.max_s,
        "format": rs.aspect.ratio or "9:16",
        "platforms": rs.platforms,
        "language": rs.language.code,
        "captions_required": rs.captions.required,
        "caption_must_include": cp.exact_caption,
        "watermark_required": rs.logo.required,
        "logo_urls": [rs.logo.url] if rs.logo.url else [],
        "on_screen_text_required": rs.on_screen_text.required,
        "on_screen_text_must_include": rs.on_screen_text.must_include,
        "tagging_required": bool(tags),
        "tags_required": tags,
        "hashtags": cp.hashtags.ordered,
        "extra_music_forbidden": rs.audio.no_added_music or rs.audio.original_only,
        "ftc_disclosure_required": cp.ftc.required,
        "heavy_source_files": False,
        "content_source_urls": rs.content_source_urls,
        "extra_rules": "\n".join(f"- {m.text}" for m in rs.manual_checks)[:4000],
        "difficulty_notes": f"ruleset v{rs.version}: {len(rs.unsupported)} unsupported, "
                            f"{len(rs.account_requirements)} account reqs, {len(rs.manual_checks)} manual checks",
    }
