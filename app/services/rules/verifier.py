"""#43: post-render rules verifier.

One check per rule: expected vs actual and *how* it was verified. Sources:
  * worker render result (`applied` + ffprobe `probe`, worker #4),
  * render job payload (`render_spec.required`, #41),
  * the asset transcript (language, subtitles text),
  * the publication copy (title/description per platform).
Status per check: pass | fail | review (human, per clip) | n/a.
Overall: fail if any check fails, else pass (human per-clip checks are listed
and confirmed by the human who approves the publish).
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Optional

from app.services.rules.schema import RuleSet, load_ruleset

VERIFIER_VERSION = 1
PLATFORMS = ("youtube",)  # milestone 1: publish only to YouTube


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    return re.sub(r"\s+", " ", s).strip()


def _alnum(s: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", (s or "").lower())


def _has(term: str, hay: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(_norm(term)) + r"(?!\w)", hay) is not None


def _find_tag(tag: str, hay: str) -> int:
    m = re.search(r"(?<!\w)" + re.escape(tag.lower()) + r"(?!\w)", hay)
    return m.start() if m else -1


def _c(rule: str, status: str, expected: Any, actual: Any, how: str, detail: str = "") -> dict:
    return {"rule": rule, "status": status, "expected": expected, "actual": actual, "how": how, "detail": detail}


# --- copy checks (pure, reused by publish_copy tests) ------------------------

def check_copy(rs: RuleSet, platform: str, title: str, description: str, extra_text: str = "") -> list[dict]:
    cp = rs.copy_rules
    out: list[dict] = []
    text = f"{title}\n{description}"
    low = _norm(text)
    lines = [ln.strip() for ln in description.splitlines()]
    nonempty = [ln for ln in lines if ln]
    how_copy = f"copy de publicación ({platform})"
    # literal caption
    if cp.exact_caption:
        ok = _norm(cp.exact_caption) in _norm(description)
        out.append(_c("copy.exact_caption", "pass" if ok else "fail", cp.exact_caption, ok, how_copy + ": contiene literal"))
    # mentions per platform
    need = cp.mentions_by_platform.get(platform) or []
    if need:
        missing = [m for m in need if _find_tag(m, low) < 0]
        out.append(_c(f"copy.mentions.{platform}", "fail" if missing else "pass", need,
                      {"missing": missing}, how_copy + ": menciones exactas"))
    if cp.must_mention_any:
        hit = [m for m in cp.must_mention_any if _has(m, low)]
        out.append(_c("copy.must_mention_any", "pass" if hit else "fail", cp.must_mention_any, hit,
                      how_copy + ": al menos una mención"))
    # hashtags order + position
    hs = cp.hashtags
    if hs.ordered:
        dlow = _norm(description)
        found = [(_find_tag(h, dlow), h) for h in hs.ordered]
        missing = [h for i, h in found if i < 0]
        idx = [i for i, _ in found if i >= 0]
        in_order = idx == sorted(idx)
        status = "pass" if not missing and in_order else "fail"
        detail = "" if in_order else "orden distinto"
        if status == "pass" and hs.position == "after_text":
            first_tag = min(idx)
            body = dlow[:first_tag]
            if not re.search(r"[a-z0-9]", re.sub(r"#\w+|@\w+", "", body)):
                status, detail = "fail", "hashtags antes del texto"
        if status == "pass" and hs.max_extra is not None:
            all_tags = re.findall(r"#\w+", description)
            extra = [t for t in all_tags if t.lower() not in {h.lower() for h in hs.ordered}
                     and t.lower() not in {f.lower() for f in cp.ftc.tokens}]
            if len(extra) > hs.max_extra:
                status, detail = "fail", f"{len(extra)} hashtags extra > {hs.max_extra}"
        out.append(_c("copy.hashtags", status, {"ordered": hs.ordered, "position": hs.position},
                      {"missing": missing, "positions": idx}, how_copy + ": orden y posición", detail))
    # FTC
    ftc = cp.ftc
    if ftc.required or ftc.tokens:
        tokens = ftc.tokens or ["#ad"]
        line_idx = next((i for i, ln in enumerate(nonempty)
                         if any(t.lower() in ln.lower() for t in tokens)), None)
        status, detail = ("fail", "falta el marcador") if line_idx is None else ("pass", "")
        if line_idx is not None and ftc.own_line:
            ln = nonempty[line_idx]
            if _norm(ln) not in {t.lower() for t in tokens}:
                status, detail = "fail", f"no está en línea propia: {ln[:60]!r}"
        if line_idx is not None and ftc.first_after_text and status == "pass":
            if line_idx == 0:
                status, detail = "fail", "va antes del texto"
        out.append(_c("copy.ftc", status, {"tokens": tokens, "own_line": ftc.own_line,
                                           "first_after_text": ftc.first_after_text},
                      {"line": line_idx}, how_copy + ": líneas del texto", detail))
    # prohibited terms (copy + burned subtitles)
    terms = rs.prohibitions.terms
    if terms:
        hay = _norm(f"{text}\n{extra_text}")
        hits = [t for t in terms if re.search(r"(?<!\w)" + re.escape(_norm(t)) + r"(?!\w)", hay)]
        out.append(_c("prohibitions.terms", "fail" if hits else "pass", terms, hits,
                      "copy + texto de subtítulos/overlay: búsqueda literal"))
    return out


# --- render checks ------------------------------------------------------------

def check_render(rs: RuleSet, *, result: dict, required: dict, duration_window: tuple[float, float],
                 clip_duration: Optional[float], tx_language: Optional[str]) -> list[dict]:
    out: list[dict] = []
    probe = result.get("probe") or {}
    applied = result.get("applied") or {}
    has_meta = bool(probe) and result.get("render_spec_version", 0) >= 2
    how_probe = "ffprobe del render (worker)"
    lo, hi = duration_window
    dur = probe.get("duration") if probe.get("duration") is not None else clip_duration
    if dur is None:
        out.append(_c("duration", "fail", [lo, hi], None, how_probe, "sin duración"))
    else:
        out.append(_c("duration", "pass" if lo - 0.25 <= float(dur) <= hi + 0.25 else "fail", [lo, hi], dur, how_probe))
    if not has_meta:
        out.append(_c("render.metadata", "fail", "render_spec v2 (applied + probe)", None,
                      "resultado del job render", "worker sin render_spec v2: actualizar worker (git pull + restart)"))
        return out
    w, h = int(required.get("width", 1080)), int(required.get("height", 1920))
    res_ok = (probe.get("width"), probe.get("height")) == (w, h)
    out.append(_c("aspect", "pass" if res_ok else "fail", f"{w}x{h}", f"{probe.get('width')}x{probe.get('height')}", how_probe))
    out.append(_c("audio", "pass" if probe.get("has_audio") else "fail", True, bool(probe.get("has_audio")), how_probe))
    if rs.audio.original_only or rs.audio.no_added_music:
        # The worker render keeps the source audio and never mixes a music track.
        extra_audio = bool(applied.get("audio_track"))
        out.append(_c("audio.original_only", "fail" if extra_audio else "pass", "audio original, sin música añadida",
                      {"extra_track": extra_audio}, "render_spec v2: el worker no mezcla pistas (applied.audio_track)"))
    cap = applied.get("captions") or {}
    frames = applied.get("frame_checks") if isinstance(applied.get("frame_checks"), dict) else None
    if rs.captions.required or required.get("captions"):
        ok = bool(cap.get("applied")) and (cap.get("events") or 0) > 0
        how_cap = "metadatos del render: eventos ASS generados desde la transcripción"
        if ok and frames is not None and "captions_visible" in frames and not frames.get("captions_visible"):
            ok = False
            how_cap = "muestreo de frames del clip renderizado (frame_checks.captions_visible)"
        out.append(_c("captions", "pass" if ok else "fail", "subtítulos quemados",
                      {"events": cap.get("events"), "captions_visible": None if frames is None else frames.get("captions_visible")},
                      how_cap))
    dictionary = list(rs.captions.brand_dictionary or required.get("brand_dictionary") or [])
    if dictionary and cap.get("text"):
        bad = []
        for word in re.findall(r"\S+", cap["text"]):
            core = re.sub(r"^\W+|\W+$", "", word)
            for d in dictionary:
                if " " not in d and _alnum(core) == _alnum(d) and core != d:
                    bad.append(core)
        out.append(_c("captions.brand_dictionary", "fail" if bad else "pass", dictionary, bad,
                      "texto de los subtítulos quemados"))
    wm = applied.get("watermark") or {}
    if rs.logo.required or required.get("logo"):
        ok = bool(wm.get("applied"))
        pos_ok = not ok or wm.get("position") == rs.logo.position
        detail = "" if ok else "sin fichero/URL de logo (confirmar con URL en la nota)"
        how_logo = "metadatos del render: overlay de logo"
        if ok and pos_ok and rs.logo.as_cta and dur is not None:
            dur_f = float(dur)
            ws = 0.0 if wm.get("start") is None else float(wm.get("start"))
            we = dur_f if wm.get("end") is None else float(wm.get("end"))
            if not (ws <= max(0.0, dur_f - 3.0) + 0.05 and we + 0.05 >= dur_f):
                ok = False
                detail = "el logo no cubre los últimos 3s del end card"
        if ok and pos_ok and frames is not None and "logo_visible" in frames and not frames.get("logo_visible"):
            ok = False
            detail = "muestreo de frames: logo no visible"
            how_logo = "muestreo de frames del clip renderizado (frame_checks.logo_visible)"
        out.append(_c("logo", "pass" if ok and pos_ok else "fail",
                      {"position": rs.logo.position, "timing": rs.logo.timing, "as_cta": rs.logo.as_cta},
                      {"applied": bool(wm.get("applied")), "position": wm.get("position"),
                       "start": wm.get("start"), "end": wm.get("end"),
                       "logo_visible": None if frames is None else frames.get("logo_visible")},
                      how_logo, detail))
    ost = applied.get("on_screen_text") or {}
    must = list(rs.on_screen_text.must_include or required.get("on_screen_must_include") or [])
    if rs.on_screen_text.required or must:
        texts = " ".join(t.get("text", "") for t in ost.get("texts") or [])
        missing = [m for m in must if _norm(m) not in _norm(texts)]
        ok = bool(ost.get("applied")) and not missing
        out.append(_c("on_screen_text", "pass" if ok else "fail", must or "texto en pantalla",
                      {"texts": [t.get("text") for t in ost.get("texts") or []], "missing": missing},
                      "metadatos del render: eventos de overlay"))
    if rs.language.code:
        if tx_language:
            ok = tx_language.lower()[:2] == rs.language.code.lower()[:2]
            out.append(_c("language", "pass" if ok else "fail", rs.language.code, tx_language, "idioma detectado por WhisperX"))
        else:
            out.append(_c("language", "review", rs.language.code, None, "sin idioma en la transcripción"))
    return out


def hook_end_in_clip(hook_end: Optional[float], clip_duration: Optional[float]) -> Optional[float]:
    """Keep hook_end only when 0 < hook_end <= clip duration. Otherwise it is absent (#65)."""
    if hook_end is None or clip_duration is None:
        return None
    try:
        value = float(hook_end)
        duration = float(clip_duration)
    except (TypeError, ValueError):
        return None
    if value <= 0 or value > duration:
        return None
    return value


def human_checks(rs: RuleSet, hook_end: Optional[float] = None, clip_duration: Optional[float] = None) -> list[dict]:
    out = []
    if rs.hook.required:
        # No usable hook_end → stay a human review. Never invent a pass (#56, #65).
        bounded = hook_end_in_clip(hook_end, clip_duration)
        if bounded is None or rs.hook.max_seconds is None:
            out.append(_c("hook", "review", rs.hook.max_seconds, hook_end, "humano al aprobar el publish", rs.hook.note or ""))
        else:
            limit = float(rs.hook.max_seconds)
            ok = bounded <= limit + 0.05
            out.append(_c("hook", "pass" if ok else "fail", limit, bounded,
                          "hook_end del decider <= hook.max_seconds",
                          "" if ok else f"hook_end {bounded} > {limit}s"))
    if rs.edit.required:
        out.append(_c("edit", "review", rs.edit.allowed_edits, None, "humano al aprobar el publish"))
    if rs.copy_rules.pinned_comment:
        out.append(_c("copy.pinned_comment", "review", rs.copy_rules.pinned_comment, None,
                      "humano: la API de YouTube no permite fijar comentarios"))
    for t in rs.prohibitions.topics:
        out.append(_c("prohibitions.topic", "review", t, None, "humano al aprobar el publish"))
    for m in rs.manual_checks:
        out.append(_c("manual_check", "review", m.text, None, "humano al aprobar el publish"))
    return out


def burned_text(result: dict) -> str:
    """Subtitles + on-screen text the worker burned into the clip."""
    applied = (result or {}).get("applied") or {}
    return " ".join([str((applied.get("captions") or {}).get("text") or "")]
                    + [str(t.get("text", "")) for t in (applied.get("on_screen_text") or {}).get("texts") or []])


def verify(rs: RuleSet, *, result: dict, required: dict, duration_window: tuple[float, float],
           clip_duration: Optional[float], tx_language: Optional[str], copies: dict[str, tuple[str, str]],
           overlay_text: Optional[str] = None, hook_end: Optional[float] = None) -> dict:
    if overlay_text is None:
        overlay_text = burned_text(result)
    checks = check_render(rs, result=result, required=required, duration_window=duration_window,
                          clip_duration=clip_duration, tx_language=tx_language)
    for platform, (title, description) in copies.items():
        for c in check_copy(rs, platform, title, description, overlay_text):
            c["platform"] = platform
            checks.append(c)
    if any(str(p).lower() == "x" for p in (rs.platforms or [])):
        checks.append(_c("copy.platform.x", "n/a", None, None,
                         "X queda fuera de alcance (#54); no bloquea YouTube, TikTok ni Instagram"))
    checks += human_checks(rs, hook_end=hook_end, clip_duration=clip_duration)
    failed = [c for c in checks if c["status"] == "fail"]
    return {
        "status": "fail" if failed else "pass",
        "version": VERIFIER_VERSION,
        "ruleset_version": rs.version,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "failed": [c["rule"] for c in failed],
        "review": [c["rule"] for c in checks if c["status"] == "review"],
        "checks": checks,
    }


def _hook_end(meta: dict) -> Optional[float]:
    raw = (meta or {}).get("hook_end")
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def verify_clip(db, clip) -> dict:
    """Load everything for `clip`, verify, store on the clip (no commit)."""
    from app.models.asset import Asset
    from app.models.campaign import Campaign
    from app.models.candidate import Candidate
    from app.models.job import Job
    from app.services.publish_copy import copy_platforms, platform_copy
    from app.services.rules.runtime import duration_window

    campaign = db.get(Campaign, clip.campaign_id)
    rs = load_ruleset((campaign.source_metadata if campaign else {}) or {}) or RuleSet()
    job = db.get(Job, clip.render_job_id) if clip.render_job_id else None
    result = (job.result if job and isinstance(job.result, dict) else {}) or {}
    payload = (job.payload if job and isinstance(job.payload, dict) else {}) or {}
    required = ((payload.get("render_spec") or {}).get("required")) or {}
    asset = db.get(Asset, clip.asset_id) if clip.asset_id else None
    tx = ((asset.extra_metadata or {}).get("transcription") or {}) if asset else {}
    cand = db.get(Candidate, clip.candidate_id) if clip.candidate_id else None
    copies = {}
    for p in copy_platforms(rs.platforms):
        title, description, _tags = platform_copy(campaign, clip, cand, p)
        copies[p] = (title, description)
    hook_end = _hook_end((cand.extra_metadata if cand is not None else None) or {})
    report = verify(rs, result=result, required=required,
                    duration_window=duration_window(campaign) if campaign else (15.0, 45.0),
                    clip_duration=clip.duration_seconds, tx_language=tx.get("language"),
                    copies=copies, hook_end=hook_end)
    clip.compliance_status = report["status"]
    clip.compliance_report = report
    return report
