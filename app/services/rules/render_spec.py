"""#41: render_spec v2 — what the worker must burn into the clip, from the RuleSet.

Same keys as the Windows worker (Parewebs0/clipping-windows-worker#4):
  captions{enabled, segments[{start,end,text,words}], brand_dictionary, style}
  watermark{enabled, url, position, width, start, end}
  on_screen_text{enabled, items[{text,start,end,position}]}
  render_spec{version, required{...}}  ← read back by the post-render verifier
Times are relative to the clip start.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from app.services.rules.schema import RuleSet, load_ruleset

RENDER_SPEC_VERSION = 2
_URL = re.compile(r"https?://\S+")
DEFAULT_LOGO_SECONDS = 3.0


def clip_segments(asset, start: float, end: float) -> list[dict]:
    """Transcript segments overlapping [start, end], shifted to clip time."""
    tx = ((getattr(asset, "extra_metadata", None) or {}).get("transcription") or {})
    out: list[dict] = []
    dur = end - start
    for seg in tx.get("segments") or []:
        try:
            s0, s1 = float(seg.get("start", 0)), float(seg.get("end", 0))
        except (TypeError, ValueError):
            continue
        if s1 <= start or s0 >= end:
            continue
        words = []
        for w in seg.get("words") or []:
            try:
                ws, we = float(w["start"]), float(w["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if we <= start or ws >= end:
                continue
            words.append({"start": round(max(0.0, ws - start), 3), "end": round(min(dur, we - start), 3),
                          "word": str(w.get("word") or w.get("text") or "").strip()})
        text = str(seg.get("text") or "").strip()
        if words:
            text = " ".join(w["word"] for w in words)
        elif s0 < start or s1 > end:
            # partial segment without word timing: keep the proportional slice of words
            toks = text.split()
            if toks and s1 > s0:
                a = int(max(0.0, (start - s0) / (s1 - s0)) * len(toks))
                b = int(min(1.0, (end - s0) / (s1 - s0)) * len(toks) + 0.999)
                text = " ".join(toks[a:b])
        if not text:
            continue
        out.append({"start": round(max(0.0, s0 - start), 3), "end": round(min(dur, s1 - start), 3),
                    "text": text, **({"words": words} if words else {})})
    return out


def logo_url(campaign, rs: RuleSet) -> Optional[str]:
    if rs.logo.url:
        return rs.logo.url
    conf = ((campaign.source_metadata or {}).get("rules_confirmations") or {}).get("human:logo_file") or {}
    m = _URL.search(str(conf.get("note") or ""))
    return m.group(0).rstrip(").,") if m else None


def build_render_spec(campaign, asset, candidate, *, captions_default: bool = True) -> dict[str, Any]:
    rs = load_ruleset(campaign.source_metadata or {}) or RuleSet()
    start, end = float(candidate.start_time), float(candidate.end_time)
    dur = round(end - start, 3)
    segs = clip_segments(asset, start, end)
    cap_enabled = bool(segs) and (rs.captions.required or captions_default)
    captions = {
        "enabled": cap_enabled,
        "segments": segs if cap_enabled else [],
        "brand_dictionary": list(rs.captions.brand_dictionary),
        "style": {"position": "bottom", "max_words": 4},
    }
    # Logo
    url = logo_url(campaign, rs) if rs.logo.required else None
    secs = float(rs.logo.min_seconds or DEFAULT_LOGO_SECONDS)
    wm_start: Optional[float] = None
    wm_end: Optional[float] = None
    if rs.logo.timing == "start":
        wm_start, wm_end = 0.0, min(dur, secs)
    elif rs.logo.timing == "end":
        wm_start, wm_end = max(0.0, dur - secs), dur
    watermark = {"enabled": bool(url), "url": url, "position": rs.logo.position, "width": 220,
                 "start": wm_start, "end": wm_end}
    # On-screen text: decider text + literal must_include + CTA
    items: list[dict] = []
    decided = str(((candidate.extra_metadata or {}).get("on_screen_text")) or "").strip()
    if decided:
        items.append({"text": decided[:120], "start": 0.0, "end": min(dur, 4.0), "position": "top"})
    have = " ".join(i["text"].lower() for i in items)
    for m in rs.on_screen_text.must_include:
        if m.lower() not in have:
            items.append({"text": m[:120], "start": 0.0, "end": dur, "position": "top"})
            have += " " + m.lower()
    if rs.logo.as_cta and rs.logo.cta_text:
        items.append({"text": rs.logo.cta_text[:120], "start": max(0.0, dur - 3.0), "end": dur, "position": "center"})
    on_screen_text = {"enabled": bool(items), "items": items}
    required = {
        "captions": bool(rs.captions.required),
        "brand_dictionary": list(rs.captions.brand_dictionary),
        "logo": bool(rs.logo.required),
        "logo_url_available": bool(url),
        "on_screen_text": bool(rs.on_screen_text.required),
        "on_screen_must_include": list(rs.on_screen_text.must_include),
        "audio": True,
        "width": 1080, "height": 1920,
    }
    return {
        "captions": captions,
        "watermark": watermark,
        "on_screen_text": on_screen_text,
        "render_spec": {"version": RENDER_SPEC_VERSION, "ruleset_version": rs.version, "required": required},
    }
