"""#41 / MOLI-35: render_spec v3 — what the worker must burn into the clip.

Same keys as the Windows worker, plus loudnorm policy:
  captions{enabled, segments[{start,end,text,words}], brand_dictionary, style}
  watermark{enabled, url, position, width, start, end}
  on_screen_text{enabled, items[{text,start,end,position}]}
  audio{original_only, normalize, target_lufs, true_peak_db}
  reframe{mode, layout, smoothing, sample_fps, speakers}
      mode=center + layout=single keeps the worker's historical center crop.
  render_spec{version, required{...}}  ← read back by the post-render verifier
Times are relative to the clip start.
v2 worker results stay valid; v3 adds applied.audio (loudnorm -14 LUFS / -1 dBTP
unless audio.original_only).
"""
from __future__ import annotations

import re
from typing import Any, Optional

from app.services.rules.schema import RuleSet, load_ruleset

RENDER_SPEC_VERSION = 3
LOUDNORM_TARGET_LUFS = -14
LOUDNORM_TRUE_PEAK_DB = -1
_OUTPUT_SIZES = {"9:16": (1080, 1920), "1:1": (1080, 1080), "16:9": (1920, 1080)}
_REFRAME_MODES = {"center", "face", "object", "auto"}
_REFRAME_LAYOUTS = {"single", "split", "screenshare", "fit", "auto"}
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
        item = {"start": round(max(0.0, s0 - start), 3), "end": round(min(dur, s1 - start), 3),
                "text": text, **({"words": words} if words else {})}
        if seg.get("speaker"):
            item["speaker"] = str(seg["speaker"])
        out.append(item)
    return out


def speaker_segments(asset, start: float, end: float) -> list[dict]:
    """Diarized turns overlapping the clip, shifted to clip time."""
    tx = ((getattr(asset, "extra_metadata", None) or {}).get("transcription") or {})
    dur = end - start
    out: list[dict] = []
    for seg in tx.get("segments") or []:
        if not isinstance(seg, dict) or not seg.get("speaker"):
            continue
        try:
            s0, s1 = float(seg.get("start", 0)), float(seg.get("end", 0))
        except (TypeError, ValueError):
            continue
        if s1 <= start or s0 >= end:
            continue
        out.append({
            "start": round(max(0.0, s0 - start), 3),
            "end": round(min(dur, s1 - start), 3),
            "speaker": str(seg["speaker"]),
        })
    return out


def reframe_config(campaign, asset, start: float, end: float) -> dict[str, Any]:
    """Default keeps today's center crop. Operators opt in via rules_overrides.reframe."""
    cfg: dict[str, Any] = {
        "mode": "center",
        "layout": "single",
        "smoothing": {"ema": 0.45, "deadzone_frac": 0.03, "max_shift_frac": 0.05},
        "sample_fps": 8,
        "speakers": speaker_segments(asset, start, end),
    }
    overrides = ((getattr(campaign, "source_metadata", None) or {}).get("rules_overrides") or {})
    custom = overrides.get("reframe") if isinstance(overrides, dict) else None
    if not isinstance(custom, dict):
        return cfg
    mode = str(custom.get("mode") or "").strip().lower()
    layout = str(custom.get("layout") or "").strip().lower()
    if mode in _REFRAME_MODES:
        cfg["mode"] = mode
    if layout in _REFRAME_LAYOUTS:
        cfg["layout"] = layout
    smoothing = custom.get("smoothing")
    if isinstance(smoothing, dict):
        merged = dict(cfg["smoothing"])
        for key in ("ema", "deadzone_frac", "max_shift_frac"):
            if key in smoothing:
                try:
                    merged[key] = float(smoothing[key])
                except (TypeError, ValueError):
                    pass
        cfg["smoothing"] = merged
    elif isinstance(smoothing, (int, float)):
        cfg["smoothing"] = {**cfg["smoothing"], "ema": float(smoothing)}
    if custom.get("sample_fps") is not None:
        try:
            cfg["sample_fps"] = min(10.0, max(5.0, float(custom["sample_fps"])))
        except (TypeError, ValueError):
            pass
    return cfg


_DRIVE_FILE = re.compile(r"drive\.google\.com/(?:file/d/|open\?id=|uc\?(?:export=download&)?id=)([\w-]{10,})")


def direct_image_url(url: Optional[str]) -> Optional[str]:
    """Google Drive 'view' links → direct download (the worker needs the bytes)."""
    if not url:
        return url
    m = _DRIVE_FILE.search(url)
    if m:
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}"
    return url


def logo_url(campaign, rs: RuleSet) -> Optional[str]:
    return direct_image_url(_logo_url(campaign, rs))


def _logo_url(campaign, rs: RuleSet) -> Optional[str]:
    overrides = ((getattr(campaign, "source_metadata", None) or {}).get("rules_overrides") or {})
    if isinstance(overrides, dict) and overrides.get("logo_url"):
        return str(overrides["logo_url"])
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
    if rs.logo.as_cta and not (wm_start is None and wm_end is None):
        # End card stays 3s. Extend a partial window so the logo covers it (#56).
        # start/end None already means the whole clip, which covers the last 3s.
        base = 0.0 if wm_start is None else float(wm_start)
        wm_start = round(min(base, max(0.0, dur - 3.0)), 3)
        wm_end = dur
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
    original_only = bool(rs.audio.original_only)
    audio = {
        "original_only": original_only,
        "normalize": not original_only,
        "target_lufs": LOUDNORM_TARGET_LUFS,
        "true_peak_db": LOUDNORM_TRUE_PEAK_DB,
    }
    out_w, out_h = _OUTPUT_SIZES.get(rs.aspect.ratio or "9:16", (1080, 1920))
    required = {
        "captions": bool(rs.captions.required),
        "brand_dictionary": list(rs.captions.brand_dictionary),
        "logo": bool(rs.logo.required),
        "logo_url_available": bool(url),
        "on_screen_text": bool(rs.on_screen_text.required),
        "on_screen_must_include": list(rs.on_screen_text.must_include),
        "audio": True,
        "width": out_w, "height": out_h,
    }
    return {
        "captions": captions,
        "watermark": watermark,
        "on_screen_text": on_screen_text,
        "audio": audio,
        "reframe": reframe_config(campaign, asset, start, end),
        "render_spec": {"version": RENDER_SPEC_VERSION, "ruleset_version": rs.version, "required": required},
    }
