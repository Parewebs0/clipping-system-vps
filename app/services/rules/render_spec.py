"""#41 / MOLI-35: render_spec v3 — what the worker must burn into the clip.

Same keys as the Windows worker, plus loudnorm policy:
  captions{enabled, segments[{start,end,text,words}], brand_dictionary, style,
           keywords, emojis}
  captions.style{preset, highlight_color, uppercase?, font?, emoji, censor}
    preset classic (default) is the static burn-in. karaoke | pop | bold opt in.
  watermark{enabled, url, position, width, start, end}
  on_screen_text{enabled, entrance, items[{text,start,end,position,entrance?}]}
  audio{original_only, normalize, target_lufs, true_peak_db}
  decider{chunk_chars, candidates_per_chunk, top_n, overlap}  ← VPS clip-decider; worker ignores
  render_spec{version, required{...}}  ← read back by the post-render verifier
Times are relative to the clip start.
v2 worker results stay valid; v3 adds applied.audio (loudnorm -14 LUFS / -1 dBTP
unless audio.original_only).
"""
from __future__ import annotations

import re
from typing import Any, Optional

from app.services.decider import DeciderOptions, decider_options
from app.services.rules.schema import RuleSet, load_ruleset

RENDER_SPEC_VERSION = 3
LOUDNORM_TARGET_LUFS = -14
LOUDNORM_TRUE_PEAK_DB = -1
# MOLI-48: same keys as the Windows worker (`app/jobs/render.py`).
# chunk_chars=0 restores the old first-80-segments / 5 000-char window.
DEFAULT_DECIDER = DeciderOptions().as_dict()
_URL = re.compile(r"https?://\S+")
DEFAULT_LOGO_SECONDS = 3.0
_CAPTION_PRESETS = {"classic", "karaoke", "pop", "bold"}
_DEFAULT_HIGHLIGHT = "&H0000FFFF"


def _clip_word(w: dict, start: float, end: float, dur: float, seg_speaker: Optional[str]) -> Optional[dict]:
    try:
        ws, we = float(w["start"]), float(w["end"])
    except (KeyError, TypeError, ValueError):
        return None
    if we <= start or ws >= end:
        return None
    text = str(w.get("word") or w.get("text") or "").strip()
    if not text:
        return None
    entry = {
        "start": round(max(0.0, ws - start), 3),
        "end": round(min(dur, we - start), 3),
        "word": text,
    }
    speaker = w.get("speaker") or seg_speaker
    if speaker:
        entry["speaker"] = str(speaker)
    return entry


def clip_segments(asset, start: float, end: float) -> list[dict]:
    """Transcript segments overlapping [start, end], shifted to clip time."""
    tx = ((getattr(asset, "extra_metadata", None) or {}).get("transcription") or {})
    flat_words = [w for w in (tx.get("words") or []) if isinstance(w, dict)]
    out: list[dict] = []
    dur = end - start
    for seg in tx.get("segments") or []:
        try:
            s0, s1 = float(seg.get("start", 0)), float(seg.get("end", 0))
        except (TypeError, ValueError):
            continue
        if s1 <= start or s0 >= end:
            continue
        seg_speaker = str(seg["speaker"]) if seg.get("speaker") else None
        nested = [w for w in (seg.get("words") or []) if isinstance(w, dict)]
        # Older transcripts keep WhisperX timings only on transcription.words.
        if nested:
            source_words = nested
        else:
            source_words = []
            for w in flat_words:
                try:
                    ws = float(w["start"])
                except (KeyError, TypeError, ValueError):
                    continue
                if s0 <= ws < s1:
                    source_words.append(w)
        words = []
        for w in source_words:
            entry = _clip_word(w, start, end, dur, seg_speaker)
            if entry:
                words.append(entry)
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
        segment = {"start": round(max(0.0, s0 - start), 3), "end": round(min(dur, s1 - start), 3),
                   "text": text, **({"words": words} if words else {})}
        if seg_speaker:
            segment["speaker"] = seg_speaker
        out.append(segment)
    return out


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


def _rules_overrides(campaign) -> dict:
    raw = (getattr(campaign, "source_metadata", None) or {}).get("rules_overrides") or {}
    return raw if isinstance(raw, dict) else {}


def caption_style(rs: RuleSet, campaign) -> dict[str, Any]:
    """Worker style. Omitted font/uppercase means the preset's own default.

    `rules_overrides.captions_style` wins over the ruleset so a campaign can
    pick karaoke/pop/bold without re-extracting the brief.
    """
    preset = str(getattr(rs.captions, "preset", None) or "classic").strip().lower()
    if preset not in _CAPTION_PRESETS:
        preset = "classic"
    style: dict[str, Any] = {
        "position": "bottom",
        "max_words": 4,
        "preset": preset,
        "highlight_color": str(rs.captions.highlight_color or _DEFAULT_HIGHLIGHT),
        "emoji": bool(rs.captions.emoji),
        "censor": {
            "words": [str(w).strip() for w in (rs.captions.censor_words or []) if str(w).strip()],
            "beep": bool(rs.captions.censor_beep),
        },
    }
    if rs.captions.uppercase is not None:
        style["uppercase"] = bool(rs.captions.uppercase)
    if rs.captions.font:
        style["font"] = str(rs.captions.font).strip()
    custom = _rules_overrides(campaign).get("captions_style")
    if isinstance(custom, dict):
        if custom.get("preset"):
            name = str(custom["preset"]).strip().lower()
            if name in _CAPTION_PRESETS:
                style["preset"] = name
        if custom.get("highlight_color"):
            style["highlight_color"] = str(custom["highlight_color"])
        if "uppercase" in custom and custom["uppercase"] is not None:
            style["uppercase"] = bool(custom["uppercase"])
        if custom.get("font"):
            style["font"] = str(custom["font"]).strip()
        if "emoji" in custom and custom["emoji"] is not None:
            style["emoji"] = bool(custom["emoji"])
        censor = custom.get("censor")
        if isinstance(censor, dict):
            style["censor"] = {
                "words": [str(w).strip() for w in (censor.get("words") or []) if str(w).strip()],
                "beep": bool(censor.get("beep")),
            }
        elif isinstance(censor, list):
            style["censor"] = {"words": [str(w).strip() for w in censor if str(w).strip()], "beep": False}
    return style


def _meta_list(meta: dict, key: str, limit: int) -> list:
    raw = meta.get(key) if isinstance(meta, dict) else None
    if not isinstance(raw, list):
        return []
    return list(raw)[:limit]


def caption_keywords(meta: dict) -> list[str]:
    """Up to 3 short words. Blanks and overlong tokens are skipped, not truncated."""
    raw = meta.get("keywords") if isinstance(meta, dict) else None
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        text = str(item or "").strip()
        if not text or len(text) > 40:
            continue
        out.append(text)
        if len(out) >= 3:
            break
    return out


def caption_emojis(meta: dict, start: float, end: float) -> list[dict]:
    """Decider emoji cues, shifted to clip time when they carry absolute times."""
    out: list[dict] = []
    dur = end - start
    for item in _meta_list(meta, "emojis", 4):
        if isinstance(item, str):
            item = {"emoji": item}
        if not isinstance(item, dict):
            continue
        emoji = str(item.get("emoji") or item.get("char") or "").strip()
        if not emoji or len(emoji) > 16 or not any(ord(ch) > 127 for ch in emoji):
            continue
        cue: dict[str, Any] = {"emoji": emoji}
        keyword = str(item.get("keyword") or "").strip()
        if keyword:
            cue["keyword"] = keyword[:40]
        if item.get("start") is not None:
            try:
                rel = float(item["start"]) - start
                rel_end = float(item["end"]) - start if item.get("end") is not None else rel + 1.2
            except (TypeError, ValueError):
                rel = None
            else:
                if rel < dur and rel_end > 0:
                    cue["start"] = round(max(0.0, rel), 3)
                    cue["end"] = round(min(dur, max(rel_end, rel + 0.4)), 3)
        if "keyword" in cue or "start" in cue:
            out.append(cue)
        if len(out) >= 2:
            break
    return out


def build_render_spec(campaign, asset, candidate, *, captions_default: bool = True) -> dict[str, Any]:
    rs = load_ruleset(campaign.source_metadata or {}) or RuleSet()
    start, end = float(candidate.start_time), float(candidate.end_time)
    dur = round(end - start, 3)
    segs = clip_segments(asset, start, end)
    cap_enabled = bool(segs) and (rs.captions.required or captions_default)
    meta = candidate.extra_metadata or {}
    if not isinstance(meta, dict):
        meta = {}
    captions = {
        "enabled": cap_enabled,
        "segments": segs if cap_enabled else [],
        "brand_dictionary": list(rs.captions.brand_dictionary),
        "style": caption_style(rs, campaign),
        "keywords": caption_keywords(meta),
        "emojis": caption_emojis(meta, start, end),
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
    # On-screen text: decider text + literal must_include + CTA.
    # entrance animates the hook only. Default off = the static overlay.
    entrance = bool(rs.on_screen_text.entrance)
    if "on_screen_entrance" in _rules_overrides(campaign):
        entrance = bool(_rules_overrides(campaign).get("on_screen_entrance"))
    items: list[dict] = []
    decided = str((meta.get("on_screen_text")) or "").strip()
    if decided:
        hook = {"text": decided[:120], "start": 0.0, "end": min(dur, 4.0), "position": "top"}
        if entrance:
            hook["entrance"] = True
        items.append(hook)
    have = " ".join(i["text"].lower() for i in items)
    for m in rs.on_screen_text.must_include:
        if m.lower() not in have:
            items.append({"text": m[:120], "start": 0.0, "end": dur, "position": "top"})
            have += " " + m.lower()
    if rs.logo.as_cta and rs.logo.cta_text:
        items.append({"text": rs.logo.cta_text[:120], "start": max(0.0, dur - 3.0), "end": dur, "position": "center"})
    on_screen_text = {"enabled": bool(items), "entrance": entrance, "items": items}
    original_only = bool(rs.audio.original_only)
    audio = {
        "original_only": original_only,
        "normalize": not original_only,
        "target_lufs": LOUDNORM_TARGET_LUFS,
        "true_peak_db": LOUDNORM_TRUE_PEAK_DB,
    }
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
        "audio": audio,
        "decider": decider_options(campaign).as_dict(),
        "render_spec": {"version": RENDER_SPEC_VERSION, "ruleset_version": rs.version, "required": required},
    }
