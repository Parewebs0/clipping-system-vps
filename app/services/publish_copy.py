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


CAPTION_MAX = {"youtube": 5000, "tiktok": 2200, "instagram": 2200}
TITLE_MAX = {"youtube": 100, "tiktok": 150, "instagram": 150}
COPY_PLATFORMS = ("youtube", "tiktok", "instagram")


def copy_platforms(platforms: list | None) -> list[str]:
    """Platforms we actually build copy for. X is ignored (#54)."""
    named = [str(p).lower() for p in (platforms or []) if p]
    if not named:
        return ["youtube"]
    return [p for p in COPY_PLATFORMS if p in named]


def _hashtags(campaign: Any, *, platform: str = "youtube") -> list[str]:
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
    if platform == "youtube" and "#Shorts" not in tags:
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

    tags = _hashtags(campaign, platform="youtube")
    description = f"{caption}\n\n{' '.join(tags)}"[: CAPTION_MAX["youtube"]]
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
    if platform != "youtube":
        legacy_tags = [t for t in legacy_tags if t.lower() != "#shorts"]
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
    if mentions:
        # Own block so a long body is shortened without dropping the tags (#54).
        blocks.append(" ".join(mentions))
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
    limit = CAPTION_MAX.get(platform, CAPTION_MAX["youtube"])
    description = _fit(blocks, limit)
    return title[: TITLE_MAX.get(platform, 100)], description, tags


def _fit(blocks: list[str], limit: int) -> str:
    """Keep mentions, the FTC line and hashtags; shorten the body so the caption fits."""
    if len(blocks) <= 1:
        return (blocks[0] if blocks else "")[:limit]
    tail = "\n".join(blocks[1:])
    if len(tail) >= limit:
        return tail[:limit]
    room = limit - len(tail) - 1
    body = blocks[0][: max(0, room)].rstrip()
    return f"{body}\n{tail}" if body else tail


def paid_promotion(campaign: Any) -> bool:
    """Clipping campaigns pay per view → YouTube paid promotion flag (#45)."""
    if campaign is None or getattr(campaign, "source_provider", None) == "manual":
        return False
    return True


def platform_mentions(campaign: Any, platform: str) -> list[str]:
    rs = _rs(campaign)
    return list(rs.copy_rules.mentions_by_platform.get(platform) or []) if rs else []


def copy_targets(rs: Any) -> list[str]:
    """Platforms whose caption the decider must satisfy. X is ignored (#54)."""
    if rs is None:
        return ["youtube"]
    return copy_platforms(getattr(rs, "platforms", None)) or ["youtube"]


def copy_rules_for_prompt(rs: Any) -> str:
    """Copy rules the decider prompt must state before it asks for a caption.

    Covers ordered hashtags, mentions per platform, FTC, exact_caption and
    prohibited terms.
    """
    if rs is None:
        return "- No campaign copy rules. Write 2-3 caption lines. No prices. Do not start with [whop]."
    cp = rs.copy_rules
    targets = copy_targets(rs)
    lines = [
        "Write ONE title and ONE caption. The same caption is checked on every platform listed here.",
        "- Platforms checked: " + ", ".join(targets),
    ]
    if cp.exact_caption:
        lines.append("- exact_caption: the caption must contain this text verbatim: " + cp.exact_caption.strip())
    if cp.must_mention_any:
        lines.append("- must_mention_any: the title or caption must include at least one of: " + ", ".join(cp.must_mention_any))
    for platform, mentions in (cp.mentions_by_platform or {}).items():
        if mentions:
            lines.append(f"- mentions ({platform}): include each of these exactly: " + ", ".join(mentions))
    hs = cp.hashtags
    if hs.ordered:
        ordered = " ".join(h if str(h).startswith("#") else f"#{h}" for h in hs.ordered)
        where = "after the caption text, never before it" if hs.position == "after_text" else "anywhere in the caption"
        if hs.max_extra == 0:
            extra = "No extra hashtags."
        elif hs.max_extra is not None:
            extra = f"At most {hs.max_extra} extra hashtags."
        else:
            extra = "Extra hashtags are allowed."
        lines.append(f"- hashtags: use these in this order, {where}: {ordered}. {extra}")
    ftc = cp.ftc
    if ftc.required or ftc.tokens:
        tokens = ftc.tokens or ["#Ad"]
        bits = [f"include one of: {', '.join(tokens)}"]
        if ftc.own_line:
            bits.append("on its own line, with nothing else on that line")
        if ftc.first_after_text:
            bits.append("as the first element after the caption text, not before the text")
        lines.append("- FTC: " + "; ".join(bits) + ".")
    if rs.prohibitions.terms:
        lines.append("- prohibitions: the title, caption and on_screen_text must not contain: " + ", ".join(rs.prohibitions.terms))
    lines.append("- No prices. Do not start with [whop].")
    return "\n".join(lines)


def format_copy_failures(failures: list[dict]) -> str:
    """One line the retry prompt can quote. Rule name plus the verifier detail."""
    parts: list[str] = []
    for row in failures:
        detail = str(row.get("detail") or "").strip()
        rule = str(row.get("rule") or "copy")
        parts.append(f"{rule} ({detail})" if detail else rule)
    return "; ".join(parts)


def copy_failures(rs: Any, title: str, description: str, extra_text: str = "") -> list[dict]:
    """Failed ``check_copy`` rows for every platform this campaign publishes to."""
    if rs is None:
        return []
    from app.services.rules.verifier import check_copy

    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for platform in copy_targets(rs):
        for row in check_copy(rs, platform, title, description, extra_text):
            if row.get("status") != "fail":
                continue
            key = (str(row.get("rule") or ""), str(row.get("detail") or ""))
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
    return out


def _clip_text(clip: dict) -> tuple[str, str, str]:
    title = str(clip.get("title") or "")[:100]
    caption = str(clip.get("caption") or clip.get("reason") or "")[:800]
    overlay = str(clip.get("on_screen_text") or "")[:120]
    return title, caption, overlay


def review_clip_copies(clips: list[dict], rs: Any, *, retry) -> list[dict]:
    """Validate each clip caption. Retry the model once when any clip fails.

    ``retry(failure_text)`` is called at most once and must return the
    replacement clip list. A clip that still fails is ``copy_status``
    ``needs_review`` and ``copy_reason`` names the failed checks.
    Without a RuleSet every clip is pending and ``retry`` is not called.
    """
    def _annotate(clip: dict, attempts: int) -> dict:
        title, caption, overlay = _clip_text(clip)
        fails = copy_failures(rs, title, caption, overlay)
        out = dict(clip)
        out["title"] = title
        out["caption"] = caption
        out["on_screen_text"] = overlay
        out["copy_status"] = "needs_review" if fails else "pending"
        out["copy_reason"] = format_copy_failures(fails) if fails else None
        out["copy_attempts"] = attempts
        return out

    attempts = 1
    if rs is not None and retry is not None:
        first = [_annotate(c, 1) for c in clips]
        failed = [f"clip {i}: {c['copy_reason']}" for i, c in enumerate(first) if c["copy_reason"]]
        if failed:
            replacement = retry("\n".join(failed))
            if isinstance(replacement, list) and replacement:
                clips = replacement
            attempts = 2
    return [_annotate(c, attempts) for c in clips]


def clip_score(
    length: float,
    dmin: float,
    dmax: float,
    model_score: Any = None,
    subscores: Any = None,
) -> float:
    """Confidence in ``[0, 1]``, rounded to 2 decimals. Not a fixed constant.

    Duration fit is ``1`` when ``length`` is the midpoint of ``[dmin, dmax]``,
    and it falls linearly by the distance from that midpoint divided by
    ``dmax`` (0 once the window is a full ``dmax`` away from the midpoint).
    Two windows of different lengths therefore get different scores.

    When ``subscores`` carries hook/flow/value/fit (each 0–1), the result is
    their mean. Missing keys fall back to duration fit (and ``fit`` prefers
    the model ``fit`` when present). A lone numeric ``model_score`` is still
    averaged with the duration fit. Anything non-numeric is ignored.
    """
    try:
        length_f = max(0.0, float(length))
        dmax_f = float(dmax)
        dmin_f = float(dmin)
    except (TypeError, ValueError):
        return 0.0
    if dmax_f <= 0:
        duration_fit = 1.0 if length_f == 0 else 0.0
    else:
        mid = (max(dmin_f, 0.0) + dmax_f) / 2.0
        duration_fit = max(0.0, 1.0 - abs(length_f - mid) / dmax_f)
    parsed = _model_score(model_score)
    parts = _subscore_parts(subscores, duration_fit=duration_fit, fallback=parsed)
    if parts is not None:
        value = sum(parts) / len(parts)
    else:
        value = duration_fit if parsed is None else (parsed + duration_fit) / 2.0
    return round(min(1.0, max(0.0, value)), 2)


def _subscore_parts(
    raw: Any, *, duration_fit: float, fallback: float | None
) -> list[float] | None:
    if not isinstance(raw, dict):
        return None
    from app.services.decider import parse_subscores

    parsed = parse_subscores(raw if any(k in raw for k in ("hook", "flow", "value", "fit")) else {"subscores": raw})
    if all(v is None for v in parsed.values()):
        return None
    default = fallback if fallback is not None else duration_fit
    return [
        parsed["hook"] if parsed["hook"] is not None else default,
        parsed["flow"] if parsed["flow"] is not None else default,
        parsed["value"] if parsed["value"] is not None else default,
        parsed["fit"] if parsed["fit"] is not None else duration_fit,
    ]


def _model_score(raw: Any) -> float | None:
    if raw is None or isinstance(raw, bool) or raw == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value != value:  # NaN
        return None
    return min(1.0, max(0.0, value))
