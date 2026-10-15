"""B-roll, music, transitions and SFX knobs for render_spec.

Defaults keep today's render: every block is off. The worker
(`app/jobs/render.py`) reads the same keys. Campaign overrides live in
``source_metadata.production`` or ``source_metadata.ruleset.production``.

``audio.original_only`` and ``audio.no_added_music`` force music off.
"""
from __future__ import annotations

from typing import Any, Mapping

DEFAULT_BROLL: dict[str, Any] = {
    "enabled": False,
    "provider": "pexels",
    "placement": "overlay",
    "max_inserts": 2,
    "opacity": 1.0,
    "marks": [],
}
DEFAULT_MUSIC: dict[str, Any] = {
    "enabled": False,
    "track": "bed_soft",
    "volume": 0.18,
    "ducking": True,
}
DEFAULT_TRANSITIONS: dict[str, Any] = {
    "enabled": False,
    "effect": "fade",
    "duration_s": 0.4,
}
DEFAULT_SFX: dict[str, Any] = {
    "enabled": False,
    "gain": 0.35,
    "library": "cc0",
    "events": [],
}

XFADE_EFFECTS = (
    "fade",
    "wipeleft",
    "wiperight",
    "slideleft",
    "slideright",
    "circleopen",
    "dissolve",
    "smoothleft",
)
PROVIDERS = ("pexels", "pixabay")
PLACEMENTS = ("overlay", "cut")
MIN_MARK_S = 0.4
MAX_MARK_S = 8.0

# Appended to the decider prompt only when b-roll is enabled for the campaign.
BROLL_PROMPT_HINT = (
    "When a moment would play better with stock footage, add a broll array on that clip "
    "(up to 2 items). Each item has start, end, query and placement. "
    "start and end are seconds from the clip start. "
    "query is a short visual search and must not name a real person. "
    "placement is overlay or cut. "
    "Omit broll when the speaker should stay full frame.\n"
)


def _bool(raw: Any, default: bool = False) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return bool(raw)
    if isinstance(raw, str):
        value = raw.strip().lower()
        if value in {"1", "true", "yes", "on"}:
            return True
        if value in {"0", "false", "no", "off"}:
            return False
    return default


def _float(raw: Any, default: float, *, minimum: float, maximum: float) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if value != value:  # NaN
        return default
    return min(maximum, max(minimum, value))


def _int(raw: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return min(maximum, max(minimum, value))


def _choice(raw: Any, allowed: tuple[str, ...], default: str) -> str:
    value = str(raw or "").strip().lower()
    return value if value in allowed else default


def _section(campaign: Any, name: str) -> dict[str, Any]:
    meta = getattr(campaign, "source_metadata", None) or {}
    raw: dict[str, Any] = {}
    if not isinstance(meta, dict):
        return raw
    for bucket in (meta.get("production"), (meta.get("ruleset") or {}).get("production") if isinstance(meta.get("ruleset"), dict) else None):
        if isinstance(bucket, dict) and isinstance(bucket.get(name), dict):
            raw.update(bucket[name])
    return raw


def broll_requested(campaign: Any) -> bool:
    """True when this campaign asked the decider for stock-footage marks."""
    return _bool(_section(campaign, "broll").get("enabled"), False)


def normalize_broll_marks(
    raw: Any,
    *,
    clip_duration: float,
    source_start: float = 0.0,
    limit: int = 2,
) -> list[dict[str, Any]]:
    """Clip-relative marks. Source-absolute times inside the window are shifted.

    A mark needs a query or a local ``file``. Overlaps keep the earlier mark.
    """
    try:
        duration = float(clip_duration)
    except (TypeError, ValueError):
        return []
    if duration <= 0:
        return []
    try:
        origin = float(source_start or 0.0)
    except (TypeError, ValueError):
        origin = 0.0
    items = raw if isinstance(raw, list) else []
    marks: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            start = float(item.get("start"))
            end = float(item.get("end"))
        except (TypeError, ValueError):
            continue
        if 0.0 <= start < end <= duration + 0.25:
            rel_s, rel_e = start, end
        elif origin <= start < end <= origin + duration + 0.25:
            rel_s, rel_e = start - origin, end - origin
        else:
            continue
        rel_s = max(0.0, rel_s)
        rel_e = min(duration, rel_e)
        if rel_e - rel_s < MIN_MARK_S:
            continue
        if rel_e - rel_s > MAX_MARK_S:
            rel_e = rel_s + MAX_MARK_S
        query = " ".join(str(item.get("query") or "").split())[:80]
        file_path = str(item.get("file") or "").strip()
        if not query and not file_path:
            continue
        mark: dict[str, Any] = {
            "start": round(rel_s, 3),
            "end": round(rel_e, 3),
            "query": query,
            "placement": _choice(item.get("placement"), PLACEMENTS, "overlay"),
        }
        if file_path:
            mark["file"] = file_path
        if isinstance(item.get("license"), dict):
            mark["license"] = dict(item["license"])
        marks.append(mark)
    marks.sort(key=lambda m: (m["start"], m["end"]))
    kept: list[dict[str, Any]] = []
    cursor = -1.0
    for mark in marks:
        if mark["start"] < cursor:
            continue
        kept.append(mark)
        cursor = mark["end"]
        if len(kept) >= max(0, int(limit)):
            break
    return kept


def normalize_sfx_events(raw: Any, *, clip_duration: float, gain: float) -> list[dict[str, Any]]:
    """Clip-relative CC0 hits. Unknown names are dropped by the worker catalog."""
    try:
        duration = float(clip_duration)
    except (TypeError, ValueError):
        return []
    items = raw if isinstance(raw, list) else []
    out: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, str):
            item = {"name": item, "at_s": 0.0}
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("id") or "").strip().lower()
        if not name:
            continue
        try:
            at_s = float(item.get("at_s") if item.get("at_s") is not None else item.get("start") or 0.0)
        except (TypeError, ValueError):
            continue
        if at_s < 0 or at_s >= duration:
            continue
        event: dict[str, Any] = {"name": name[:40], "at_s": round(at_s, 3), "gain": gain}
        if "gain" in item:
            event["gain"] = _float(item.get("gain"), gain, minimum=0.0, maximum=1.0)
        out.append(event)
    return out[:8]


def production_blocks(
    campaign: Any,
    candidate: Any = None,
    *,
    original_only: bool = False,
    no_added_music: bool = False,
) -> dict[str, Any]:
    """Four independent render_spec blocks. Missing config stays off."""
    try:
        start = float(getattr(candidate, "start_time", 0.0) or 0.0)
        end = float(getattr(candidate, "end_time", 0.0) or 0.0)
    except (TypeError, ValueError):
        start, end = 0.0, 0.0
    duration = max(0.0, end - start)
    meta = getattr(candidate, "extra_metadata", None) or {}
    if not isinstance(meta, dict):
        meta = {}

    broll_raw = _section(campaign, "broll")
    broll = dict(DEFAULT_BROLL)
    broll["enabled"] = _bool(broll_raw.get("enabled"), False)
    broll["provider"] = _choice(broll_raw.get("provider"), PROVIDERS, "pexels")
    broll["placement"] = _choice(broll_raw.get("placement"), PLACEMENTS, "overlay")
    broll["max_inserts"] = _int(broll_raw.get("max_inserts"), 2, minimum=0, maximum=4)
    broll["opacity"] = _float(broll_raw.get("opacity"), 1.0, minimum=0.05, maximum=1.0)
    if broll["enabled"]:
        broll["marks"] = normalize_broll_marks(
            meta.get("broll_marks") if meta.get("broll_marks") is not None else meta.get("broll"),
            clip_duration=duration,
            source_start=start,
            limit=broll["max_inserts"],
        )
        for mark in broll["marks"]:
            if mark["placement"] not in PLACEMENTS:
                mark["placement"] = broll["placement"]
    else:
        broll["marks"] = []

    music_raw = _section(campaign, "music")
    music = dict(DEFAULT_MUSIC)
    requested = _bool(music_raw.get("enabled"), False)
    music["enabled"] = requested
    if music_raw.get("track"):
        music["track"] = str(music_raw["track"]).strip()[:80] or DEFAULT_MUSIC["track"]
    music["volume"] = _float(music_raw.get("volume"), 0.18, minimum=0.0, maximum=1.0)
    music["ducking"] = _bool(music_raw.get("ducking"), True)
    if requested and (original_only or no_added_music):
        music["enabled"] = False
        music["requested"] = True
        music["suppressed_reason"] = "audio.original_only" if original_only else "audio.no_added_music"

    trans_raw = _section(campaign, "transitions")
    transitions = dict(DEFAULT_TRANSITIONS)
    transitions["enabled"] = _bool(trans_raw.get("enabled"), False)
    transitions["effect"] = _choice(trans_raw.get("effect"), XFADE_EFFECTS, "fade")
    transitions["duration_s"] = _float(trans_raw.get("duration_s"), 0.4, minimum=0.1, maximum=1.5)

    sfx_raw = _section(campaign, "sfx")
    sfx = dict(DEFAULT_SFX)
    sfx["enabled"] = _bool(sfx_raw.get("enabled"), False)
    sfx["gain"] = _float(sfx_raw.get("gain"), 0.35, minimum=0.0, maximum=1.0)
    sfx["library"] = "cc0"
    if sfx["enabled"]:
        raw_events = sfx_raw.get("events")
        if raw_events is None:
            raw_events = meta.get("sfx_events") or []
        sfx["events"] = normalize_sfx_events(raw_events, clip_duration=duration or 10_000.0, gain=sfx["gain"])
    else:
        sfx["events"] = []
    return {"broll": broll, "music": music, "transitions": transitions, "sfx": sfx}


def with_broll_hint(prompt: str, ask_broll: bool) -> str:
    """Insert the stock-footage instruction before the transcript block."""
    if not ask_broll or not prompt:
        return prompt
    needle = "Transcript segments"
    idx = prompt.find(needle)
    if idx < 0:
        return prompt + BROLL_PROMPT_HINT
    return prompt[:idx] + BROLL_PROMPT_HINT + prompt[idx:]
