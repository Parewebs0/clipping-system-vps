"""YouTube Analytics per published clip, kept to calibrate ``clip_score``.

The tick reads views and audience retention. It stores them on
``clips.analytics`` and writes ``calibrated_clip_score`` next to the prior
score. The prior score on the candidate is left as the decider wrote it.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, urlparse

ANALYTICS_URL = "https://youtubeanalytics.googleapis.com/v2/reports"
TOKEN_URL = "https://oauth2.googleapis.com/token"
# Views at or above this count saturate the view factor.
VIEW_SATURATION = 10_000.0
REFRESH_HOURS = 24

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def youtube_video_id(url: str | None) -> str | None:
    """Pull the 11-character id from a watch, shorts, embed or youtu.be URL."""
    if not url:
        return None
    text = str(url).strip()
    if _VIDEO_ID.match(text):
        return text
    try:
        parsed = urlparse(text)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host == "youtu.be":
        candidate = parsed.path.strip("/").split("/")[0]
        return candidate if _VIDEO_ID.match(candidate) else None
    if not host.endswith("youtube.com"):
        return None
    query = parse_qs(parsed.query).get("v", [])
    if query and _VIDEO_ID.match(query[0]):
        return query[0]
    parts = [p for p in parsed.path.split("/") if p]
    for marker in ("shorts", "embed", "live", "v"):
        if marker in parts:
            idx = parts.index(marker)
            if idx + 1 < len(parts) and _VIDEO_ID.match(parts[idx + 1]):
                return parts[idx + 1]
    return None


def calibrate_clip_score(
    prior: Any,
    *,
    views: Any = None,
    average_view_percentage: Any = None,
) -> float | None:
    """Blend the decider score with what the clip actually did on YouTube.

    Retention (average percentage viewed) weighs 0.6 and a log-scaled view
    count weighs 0.4. The result is ``0.7 * prior + 0.3 * observed``, so one
    viral night cannot replace the decider score. ``None`` when both metrics
    are missing.
    """
    retention = _unit_percent(average_view_percentage)
    view_factor = _view_factor(views)
    if retention is None and view_factor is None:
        return None
    if retention is not None and view_factor is not None:
        observed = 0.6 * retention + 0.4 * view_factor
    else:
        observed = retention if retention is not None else view_factor
    assert observed is not None
    prior_f = _unit(prior)
    if prior_f is None:
        return round(observed, 2)
    return round(min(1.0, max(0.0, 0.7 * prior_f + 0.3 * observed)), 2)


def _unit(raw: Any) -> float | None:
    if raw is None or isinstance(raw, bool) or raw == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value != value:
        return None
    return min(1.0, max(0.0, value))


def _unit_percent(raw: Any) -> float | None:
    if raw is None or isinstance(raw, bool) or raw == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value != value:
        return None
    return min(1.0, max(0.0, value / 100.0))


def _view_factor(raw: Any) -> float | None:
    if raw is None or isinstance(raw, bool) or raw == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value != value or value < 0:
        return None
    return min(1.0, math.log10(1.0 + value) / math.log10(1.0 + VIEW_SATURATION))


def parse_views_report(body: Mapping[str, Any] | None) -> dict[str, Any]:
    """YouTube Analytics ``views,averageViewDuration,averageViewPercentage`` row."""
    empty = {"views": None, "average_view_duration_seconds": None, "average_view_percentage": None}
    if not isinstance(body, Mapping):
        return empty
    headers = [str(h.get("name") or "") for h in body.get("columnHeaders") or [] if isinstance(h, Mapping)]
    rows = body.get("rows") or []
    if not headers or not rows or not isinstance(rows[0], (list, tuple)):
        return empty
    row = list(rows[0])
    data = {headers[i]: row[i] for i in range(min(len(headers), len(row)))}
    return {
        "views": _optional_float(data.get("views")),
        "average_view_duration_seconds": _optional_float(data.get("averageViewDuration")),
        "average_view_percentage": _optional_float(data.get("averageViewPercentage")),
    }


def parse_retention_report(body: Mapping[str, Any] | None) -> list[dict[str, float]]:
    """``elapsedVideoTimeRatio`` × ``audienceWatchRatio`` points, oldest order kept."""
    if not isinstance(body, Mapping):
        return []
    headers = [str(h.get("name") or "") for h in body.get("columnHeaders") or [] if isinstance(h, Mapping)]
    if "elapsedVideoTimeRatio" not in headers or "audienceWatchRatio" not in headers:
        return []
    elapsed_i = headers.index("elapsedVideoTimeRatio")
    watch_i = headers.index("audienceWatchRatio")
    points: list[dict[str, float]] = []
    for row in body.get("rows") or []:
        if not isinstance(row, (list, tuple)):
            continue
        if max(elapsed_i, watch_i) >= len(row):
            continue
        elapsed = _optional_float(row[elapsed_i])
        watch = _optional_float(row[watch_i])
        if elapsed is None or watch is None:
            continue
        points.append({
            "elapsed_ratio": round(min(1.0, max(0.0, elapsed)), 4),
            "watch_ratio": round(max(0.0, watch), 4),
        })
    return points


def _optional_float(raw: Any) -> float | None:
    if raw is None or isinstance(raw, bool) or raw == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value != value:
        return None
    return value


def build_analytics_payload(
    *,
    video_id: str,
    metrics: Mapping[str, Any],
    retention: list[dict[str, Any]] | None,
    prior_score: Any,
    fetched_at: str,
    error: str | None = None,
) -> dict[str, Any]:
    """The document stored on ``clips.analytics``."""
    views = _optional_float(metrics.get("views")) if isinstance(metrics, Mapping) else None
    average = _optional_float(metrics.get("average_view_percentage")) if isinstance(metrics, Mapping) else None
    duration = _optional_float(metrics.get("average_view_duration_seconds")) if isinstance(metrics, Mapping) else None
    calibrated = None if error else calibrate_clip_score(
        prior_score, views=views, average_view_percentage=average
    )
    youtube: dict[str, Any] = {
        "video_id": video_id,
        "views": None if views is None else int(views) if views == int(views) else views,
        "average_view_duration_seconds": duration,
        "average_view_percentage": average,
        "retention": list(retention or []),
        "fetched_at": fetched_at,
        "source": "youtube_analytics",
    }
    if error:
        youtube["error"] = error
    score_block = None
    if calibrated is not None:
        score_block = {
            "prior": None if _unit(prior_score) is None else round(_unit(prior_score), 2),
            "calibrated": calibrated,
            "views": youtube["views"],
            "average_view_percentage": average,
        }
    return {"youtube": youtube, "clip_score": score_block}


def merge_analytics(previous: Mapping[str, Any] | None, new: Mapping[str, Any]) -> dict[str, Any]:
    """Keep a previous good read when YouTube returns no rows yet."""
    prev = dict(previous or {})
    incoming = dict(new)
    yt = incoming.get("youtube") or {}
    prev_views = ((prev.get("youtube") or {}).get("views"))
    if yt.get("error") and prev_views is not None:
        kept = dict(prev)
        kept["last_error"] = yt.get("error")
        kept["last_attempt_at"] = yt.get("fetched_at")
        return kept
    return incoming


def should_refresh(analytics: Mapping[str, Any] | None, now: datetime, *, refresh_hours: int = REFRESH_HOURS) -> bool:
    """True when this clip has no fresh YouTube read."""
    yt = (analytics or {}).get("youtube") if isinstance(analytics, Mapping) else None
    if not isinstance(yt, Mapping) or not yt.get("fetched_at") or yt.get("error"):
        return True
    fetched = _parse_time(str(yt.get("fetched_at")))
    if fetched is None:
        return True
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    return now - fetched >= timedelta(hours=max(1, int(refresh_hours)))


def _parse_time(value: str) -> datetime | None:
    text = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def apply_analytics(clip: Any, candidate: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Write the payload on the clip and the calibrated score on the candidate.

    The candidate's ``score`` column stays the decider value. Calibration is
    recorded beside it so a later ranker can read either number.
    """
    stored = merge_analytics(getattr(clip, "analytics", None), payload)
    clip.analytics = stored
    score = stored.get("clip_score") or {}
    if candidate is not None and score.get("calibrated") is not None:
        meta = dict(getattr(candidate, "extra_metadata", None) or {})
        meta["clip_score_prior"] = score.get("prior")
        meta["calibrated_clip_score"] = score.get("calibrated")
        meta["youtube_analytics"] = {
            "video_id": (stored.get("youtube") or {}).get("video_id"),
            "views": (stored.get("youtube") or {}).get("views"),
            "average_view_percentage": (stored.get("youtube") or {}).get("average_view_percentage"),
            "fetched_at": (stored.get("youtube") or {}).get("fetched_at"),
        }
        candidate.extra_metadata = meta
    return stored


def fetch_youtube_analytics(
    video_id: str,
    *,
    access_token: str,
    start_date: str,
    end_date: str,
    get: Callable[..., Any],
) -> tuple[dict[str, Any], list[dict[str, float]], str | None]:
    """Two Analytics API reports. ``get(url, params, headers)`` returns JSON."""
    headers = {"Authorization": f"Bearer {access_token}"}
    common = {
        "ids": "channel==MINE",
        "startDate": start_date,
        "endDate": end_date,
        "filters": f"video=={video_id}",
    }
    views_body = get(
        ANALYTICS_URL,
        {**common, "metrics": "views,averageViewDuration,averageViewPercentage"},
        headers,
    )
    retention_body = get(
        ANALYTICS_URL,
        {**common, "metrics": "audienceWatchRatio", "dimensions": "elapsedVideoTimeRatio"},
        headers,
    )
    metrics = parse_views_report(views_body)
    retention = parse_retention_report(retention_body)
    error = None
    if metrics.get("views") is None and not retention:
        error = "no_rows"
    return metrics, retention, error


def refresh_access_token(
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
    post: Callable[..., Any],
) -> str:
    """OAuth refresh. ``post(url, data)`` returns the token JSON."""
    body = post(TOKEN_URL, {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    })
    token = body.get("access_token") if isinstance(body, Mapping) else None
    if not token:
        raise RuntimeError("youtube token refresh returned no access_token")
    return str(token)


def sync_youtube_analytics(
    db: Any,
    *,
    fetch: Callable[[str], tuple[dict[str, Any], list, str | None]],
    limit: int = 20,
    now: datetime | None = None,
    refresh_hours: int = REFRESH_HOURS,
) -> list[dict[str, Any]]:
    """Read posted YouTube clips and store views plus retention.

    ``fetch(video_id)`` returns ``(metrics, retention, error)``. Network and
    token refresh stay in the tick so tests can pass a fake.
    """
    from app.models.candidate import Candidate
    from app.models.clip import Clip
    from app.models.clip_publication import ClipPublication

    moment = now or datetime.now(timezone.utc)
    pubs = (
        db.query(ClipPublication)
        .filter(
            ClipPublication.platform == "youtube",
            ClipPublication.status == "posted",
            ClipPublication.post_url.isnot(None),
        )
        .order_by(ClipPublication.posted_at.asc().nullsfirst())
        .limit(max(1, int(limit)) * 5)
        .all()
    )
    saved: list[dict[str, Any]] = []
    for pub in pubs:
        if len(saved) >= max(1, int(limit)):
            break
        video_id = youtube_video_id(pub.post_url)
        clip = db.get(Clip, pub.clip_id)
        if clip is None or not video_id:
            continue
        if not should_refresh(clip.analytics, moment, refresh_hours=refresh_hours):
            continue
        candidate = db.get(Candidate, clip.candidate_id) if clip.candidate_id else None
        prior = getattr(candidate, "score", None) if candidate is not None else None
        metrics, retention, error = fetch(video_id)
        payload = build_analytics_payload(
            video_id=video_id,
            metrics=metrics or {},
            retention=retention or [],
            prior_score=prior,
            fetched_at=moment.isoformat(),
            error=error,
        )
        stored = apply_analytics(clip, candidate, payload)
        saved.append({
            "clip_id": str(clip.id),
            "video_id": video_id,
            "views": (stored.get("youtube") or {}).get("views"),
            "calibrated_clip_score": (stored.get("clip_score") or {}).get("calibrated"),
            "error": (stored.get("youtube") or {}).get("error") or stored.get("last_error"),
        })
    if saved:
        db.commit()
    return saved
