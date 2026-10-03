"""Title/description for social posts. 0 LLM on the publish path."""
from __future__ import annotations

import re
from typing import Any

_WHOP_PREFIX = re.compile(
    r"^\s*\[whop\]\s*(?:·|\|)\s*\$[^·|]+(?:·|\|)\s*\$[^·|]+(?:·|\|)?\s*",
    re.IGNORECASE,
)


def _meta(campaign: Any) -> dict:
    if campaign is None:
        return {}
    sm = getattr(campaign, "source_metadata", None) or {}
    return sm if isinstance(sm, dict) else {}


def clean_campaign_title(raw: str) -> str:
    text = (raw or "").strip()
    text = _WHOP_PREFIX.sub("", text).strip(" ·|-+")
    text = re.sub(r"\s+", " ", text)
    return text[:100] if text else "clip"


def _hashtags(campaign: Any) -> list[str]:
    sm = _meta(campaign)
    discovered = sm.get("discovered") if isinstance(sm.get("discovered"), dict) else {}
    rules = sm.get("rules") if isinstance(sm.get("rules"), dict) else {}
    extra = rules.get("hashtags") or discovered.get("hashtags") or []
    if isinstance(extra, str):
        extra = extra.split()
    tags: list[str] = []
    for tag in extra:
        t = str(tag).strip()
        if not t:
            continue
        if not t.startswith("#"):
            t = "#" + t.lstrip("#")
        if t.lower() not in {h.lower() for h in tags}:
            tags.append(t)
    if "#Shorts" not in tags:
        tags.append("#Shorts")
    return tags[:8]


def youtube_copy(campaign: Any, clip: Any, candidate: Any = None) -> tuple[str, str, list[str]]:
    camp_title = clean_campaign_title(getattr(campaign, "name", None) or "")
    cmeta = {}
    if candidate is not None:
        raw = getattr(candidate, "extra_metadata", None) or {}
        if isinstance(raw, dict):
            cmeta = raw
    kind = str(cmeta.get("kind") or cmeta.get("source") or "")
    title = str(cmeta.get("title") or "").strip()[:100] or camp_title

    caption = (
        str(cmeta.get("caption") or cmeta.get("description") or "").strip()
        or str(getattr(candidate, "reasoning", None) or "").strip()
    )
    if len(caption) < 12 or kind in {"silent", "duration_cut"}:
        caption = (
            f"{camp_title}\n\n"
            "Highlight from the campaign. Watch till the end."
        )
    elif caption.lower() in {"grok", "short", "speech"}:
        caption = f"{camp_title}\n\n{caption}"

    tags = _hashtags(campaign)
    description = f"{caption}\n\n{' '.join(tags)}"[:5000]
    return title, description, tags


def _rs(campaign: Any):
    from app.services.rules.schema import load_ruleset

    return load_ruleset(_meta(campaign))


def _strip_prohibited(text: str, terms: list[str]) -> str:
    """Drop sentences of the (LLM) caption that contain a prohibited term."""
    if not terms or not text:
        return text
    pats = [re.compile(r"(?<!\w)" + re.escape(t.lower()) + r"(?!\w)") for t in terms]
    parts = re.split(r"(?<=[.!?\n])\s+", text)
    keep = [p for p in parts if not any(pt.search(p.lower()) for pt in pats)]
    return " ".join(keep).strip()


def platform_copy(campaign: Any, clip: Any, candidate: Any = None, platform: str = "youtube") -> tuple[str, str, list[str]]:
    """Copy for `platform` built from the RuleSet (#45). Shared by the
    publish enqueue and the post-render verifier (#43). 0 LLM."""
    rs = _rs(campaign)
    if rs is None:
        return youtube_copy(campaign, clip, candidate)
    cp = rs.copy_rules
    title, legacy_desc, legacy_tags = youtube_copy(campaign, clip, candidate)
    terms = list(rs.prohibitions.terms)
    title = _strip_prohibited(title, terms) or clean_campaign_title(getattr(campaign, "name", "") or "")
    # 1) body text: literal caption wins; else the decider caption (without hashtags)
    if cp.exact_caption:
        body = cp.exact_caption.strip()
    else:
        body = legacy_desc.rsplit("\n\n", 1)[0] if legacy_desc else ""
        body = _strip_prohibited(body, terms) or clean_campaign_title(getattr(campaign, "name", "") or "")
    low = body.lower()
    # 2) mandatory mentions
    if cp.must_mention_any and not any(m.lower() in low for m in cp.must_mention_any):
        body = f"{body}\n{cp.must_mention_any[0]}"
        low = body.lower()
    mentions = [m for m in (cp.mentions_by_platform.get(platform) or []) if m.lower() not in low]
    if mentions:
        body = f"{body} {' '.join(mentions)}"
    # 3) FTC disclosure
    ftc_tokens = cp.ftc.tokens or (["#Ad"] if cp.ftc.required else [])
    ftc_line = ftc_tokens[0] if ftc_tokens else None
    # 4) hashtags: ordered first (in order), then legacy extras within max_extra
    tags: list[str] = []
    for h in cp.hashtags.ordered:
        t = h if h.startswith("#") else f"#{h}"
        if t.lower() not in {x.lower() for x in tags}:
            tags.append(t)
    ftc_low = {t.lower() for t in ftc_tokens}
    extras = [t for t in legacy_tags if t.lower() not in {x.lower() for x in tags} and t.lower() not in ftc_low]
    if cp.hashtags.max_extra is not None:
        extras = extras[: max(0, cp.hashtags.max_extra)]
    tags += extras
    blocks = [body]
    if ftc_line and ftc_line.lower() in {t.lower() for t in tags} and not cp.ftc.own_line:
        # The disclosure is also the first ordered hashtag (e.g. #forgeguipartner):
        # the hashtag line right after the text satisfies "first after text".
        tags = [t for t in tags if t.lower() == ftc_line.lower()] + [t for t in tags if t.lower() != ftc_line.lower()]
        ftc_line = None
    if ftc_line and (cp.ftc.own_line or cp.ftc.first_after_text or cp.ftc.required):
        blocks.append(ftc_line)
        tags = [t for t in tags if t.lower() != ftc_line.lower()]
    if tags:
        blocks.append(" ".join(tags))
    description = "\n".join(blocks)[:5000]
    return title[:100], description, tags


def paid_promotion(campaign: Any) -> bool:
    """Clipping campaigns pay per view → YouTube paid promotion flag (#45)."""
    if campaign is None or getattr(campaign, "source_provider", None) == "manual":
        return False
    return True


def platform_mentions(campaign: Any, platform: str) -> list[str]:
    rs = _rs(campaign)
    return list(rs.copy_rules.mentions_by_platform.get(platform) or []) if rs else []
