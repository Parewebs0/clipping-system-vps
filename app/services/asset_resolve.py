"""Unified 3b backends: Drive folders, Dropbox, direct URLs, streamer channels/VODs."""
from __future__ import annotations

import heapq
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from app.services.http_retry import RETRY_STATUS, pause
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
MAX_FILES = 12          # max *videos* per campaign (only videos count)
MAX_DEPTH = 3
MAX_FOLDER_CALLS = 25   # gog ls calls per Drive root
RESOLVER_VERSION = 2    # bump to retry failed_resolve made by older resolvers

# Whop / Content Rewards uploads are stored as relative keys in a public bucket.
WHOP_PUBLIC_BASE_DEFAULT = (
    "https://content-rewards-production-publicassetsbucket-oxvzxvnr.s3.us-east-1.amazonaws.com/"
)
WHOP_REL_RE = re.compile(r"^/?(organizations/[^\s?#]+)$", re.I)

_JUNK_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}
_FOLDER_GOOD = ("clip", "raw", "rush", "extrait", "episode", "épisode", "footage", "video", "vidéo",
                "source", "podcast", "interview", "b-roll", "broll", "highlight", "stream", "vod")
_FOLDER_BAD = ("font", "police", "music", "musique", "song", "audio", "sound", "logo",
               "thumbnail", "miniature", "image", "photo", "picture", "overlay", "sfx")


def min_video_bytes() -> int:
    try:
        return int(os.environ.get("RESOLVE_MIN_VIDEO_BYTES", 1024 * 1024))
    except ValueError:
        return 1024 * 1024


def whop_public_base() -> str:
    base = os.environ.get("WHOP_PUBLIC_ASSET_BASE") or WHOP_PUBLIC_BASE_DEFAULT
    return base if base.endswith("/") else base + "/"


def normalize_url(url: str) -> str:
    """Relative Whop storage keys → public bucket URL; everything else untouched."""
    u = (url or "").strip()
    if u and not u.lower().startswith("http"):
        m = WHOP_REL_RE.match(u)
        if m:
            return whop_public_base() + m.group(1)
    return u


def is_whop_storage(url: str) -> bool:
    return (url or "").startswith(whop_public_base())


def is_junk_name(name: str) -> bool:
    base = Path(str(name or "")).name
    return base.startswith("._") or base.lower() in _JUNK_NAMES


def folder_priority(name: str) -> int:
    n = str(name or "").lower()
    if any(k in n for k in _FOLDER_BAD):
        return -1
    if any(k in n for k in _FOLDER_GOOD):
        return 1
    return 0


def _int(v: Any) -> int | None:
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def head_check(
    url: str,
    timeout: float = 15.0,
    *,
    attempts: int = 3,
    sleep=None,
) -> tuple[bool, int | None, str]:
    """HEAD a public URL. Returns (ok, content_length, content_type).

    Timeouts and 429/5xx are retried. A 4xx or a bad URL returns False
    without another attempt. Used for Whop/direct files and any public
    video URL (YouTube, TikTok, Instagram included when they are fetched).
    """
    req = urllib.request.Request(
        url, method="HEAD", headers={"User-Agent": "clipping-system-vps/1.0 (asset-resolver)"},
    )
    tries = max(1, attempts)
    for attempt in range(1, tries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (public https only)
                ctype = r.headers.get("Content-Type") or ""
                return (200 <= r.status < 300), _int(r.headers.get("Content-Length")), ctype
        except urllib.error.HTTPError as exc:
            if exc.code in RETRY_STATUS and attempt < tries:
                pause(attempt, backoff_s=0.2, sleep=sleep)
                continue
            return False, None, ""
        except ValueError:
            return False, None, ""
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt < tries:
                pause(attempt, backoff_s=0.2, sleep=sleep)
                continue
            return False, None, ""
    return False, None, ""
FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"

DRIVE_FOLDER_RE = re.compile(r"drive\.google\.com/(?:drive/(?:u/\d+/)?folders|drive/folders)/([a-zA-Z0-9_-]+)", re.I)
DRIVE_FILE_RE = re.compile(r"drive\.google\.com/(?:file/d|open)/([a-zA-Z0-9_-]+)", re.I)
DRIVE_UC_RE = re.compile(r"[?&]id=([a-zA-Z0-9_-]{20,})", re.I)
YT_RE = re.compile(
    r"(?:youtube\.com/watch\?[^\s]*v=|youtube\.com/shorts/|youtu\.be/)([A-Za-z0-9_-]{6,})",
    re.I,
)
VIMEO_RE = re.compile(r"vimeo\.com/(?:video/)?(\d+)", re.I)
TIKTOK_RE = re.compile(r"tiktok\.com/@[^/]+/video/(\d+)", re.I)
DROPBOX_FOLDER_RE = re.compile(r"dropbox\.com/scl/fo/([a-zA-Z0-9_-]+)", re.I)
DROPBOX_FILE_RE = re.compile(r"dropbox\.com/(?:scl/fi|s)/([a-zA-Z0-9_-]+)", re.I)
# Streamer sources. Single VODs are direct sources; channels resolve to their
# latest VODs (see resolve_channel_vods).
TWITCH_VOD_RE = re.compile(r"twitch\.tv/videos/(\d+)", re.I)
KICK_VOD_RE = re.compile(r"kick\.com/([A-Za-z0-9_-]+)/videos/([A-Za-z0-9-]{8,})", re.I)
_END = r"/?(?:[?#].*)?$"
YT_CHANNEL_RE = re.compile(
    r"youtube\.com/(@[A-Za-z0-9._-]+|channel/[A-Za-z0-9_-]+|c/[A-Za-z0-9._-]+|user/[A-Za-z0-9._-]+)"
    r"(?:/(videos|streams|featured))?" + _END,
    re.I,
)
TWITCH_CHANNEL_RE = re.compile(r"twitch\.tv/([A-Za-z0-9_]{2,25})(?:/videos)?" + _END, re.I)
KICK_CHANNEL_RE = re.compile(r"kick\.com/([A-Za-z0-9_-]{2,40})(?:/videos)?" + _END, re.I)
_CHANNEL_RESERVED = {
    "videos", "video", "directory", "p", "settings", "search", "downloads", "jobs", "turbo",
    "subscriptions", "inventory", "wallet", "drops", "following", "browse", "categories",
    "category", "signup", "login", "privacy", "terms", "dashboard", "clips", "clip", "help",
    "about", "community", "api", "moderator", "store", "prime",
}

# Social pages that are references (sounds/audio pages, short-links, profiles,
# posts we cannot download) rather than source footage. A campaign whose only
# links are these has nothing to ingest -> fail cleanly as "social_only".
_SOCIAL_DOMAINS = (
    "instagram.com",
    "tiktok.com",
    "twitter.com",
    "x.com",
    "facebook.com",
    "fb.watch",
    "threads.net",
    "snapchat.com",
)


def _is_social(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    if any(host == d or host.endswith("." + d) for d in _SOCIAL_DOMAINS):
        return True
    # YouTube sound/audio pages (youtube.com/source/<id>/shorts) are not footage.
    return host.endswith("youtube.com") and urlparse(url).path.lower().startswith("/source/")


_SKIP_HOST_HINTS = (
    "mediasilo.com",
    "we.tl",
    "wetransfer.com",
    "frame.io",
    "mega.nz",
)


def classify(url: str) -> str:
    url = normalize_url(url)
    u = (url or "").lower()
    if not u.startswith("http"):
        return "invalid"
    if is_whop_storage(url):
        path = urlparse(url).path.lower()
        return "whop_video" if any(path.endswith(ext) for ext in VIDEO_EXT) else "whop_other"
    if "docs.google.com/document" in u or "docs.google.com/spreadsheets" in u:
        return "brief_doc"
    if DRIVE_FOLDER_RE.search(url or ""):
        return "drive_folder"
    if "drive.google.com/uc?" in u or DRIVE_FILE_RE.search(url or ""):
        return "drive_file"
    if DROPBOX_FOLDER_RE.search(url or ""):
        return "dropbox_folder"
    if DROPBOX_FILE_RE.search(url or "") or ("dropbox.com" in u and "/scl/fo/" not in u):
        return "dropbox_file"
    if YT_RE.search(url or ""):
        return "youtube"
    if TWITCH_VOD_RE.search(url or ""):
        return "twitch_vod"
    if KICK_VOD_RE.search(url or ""):
        return "kick_vod"
    if channel_ref(url):
        return "channel"
    if TIKTOK_RE.search(url or ""):
        return "tiktok"
    if VIMEO_RE.search(url or "") and "/user" not in u:
        return "vimeo"
    path = urlparse(url).path.lower()
    if any(path.endswith(ext) for ext in VIDEO_EXT):
        return "direct"
    if any(h in u for h in _SKIP_HOST_HINTS):
        return "unsupported"
    if "youtube.com/@" in u or "youtube.com/channel" in u:
        return "profile"
    if _is_social(url):
        return "social"
    return "unknown"


def drive_file_id(url: str) -> str | None:
    m = DRIVE_FILE_RE.search(url or "")
    if m:
        return m.group(1)
    m = DRIVE_UC_RE.search(url or "")
    return m.group(1) if m else None


def drive_folder_id(url: str) -> str | None:
    m = DRIVE_FOLDER_RE.search(url or "")
    return m.group(1) if m else None


def _gog_env() -> dict:
    env = dict(os.environ)
    secret = Path("/etc/openclaw/cron-secrets.env")
    if not env.get("GOG_KEYRING_PASSWORD") and secret.exists():
        for line in secret.read_text().splitlines():
            if line.startswith("GOG_KEYRING_PASSWORD="):
                env["GOG_KEYRING_PASSWORD"] = line.split("=", 1)[1].strip().strip('"')
    return env


def _parse_gog_json(out: str) -> list[dict]:
    data = json.loads(out)
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if not isinstance(data, dict):
        return []
    val = data.get("files") or data.get("items") or []
    return [x for x in val if isinstance(x, dict)] if isinstance(val, list) else []


def gog_ls(folder_id: str) -> list[dict]:
    env = _gog_env()
    cmd = ["gog", "drive", "ls", "--parent", folder_id, "--json", "--max", "100"]
    if env.get("GOG_ACCOUNT"):
        cmd[3:3] = ["--account", env["GOG_ACCOUNT"]]
    p = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=90)
    if p.returncode != 0:
        raise RuntimeError((p.stderr or p.stdout or "gog failed")[:400])
    return _parse_gog_json((p.stdout or "").strip() or "{}")


def _is_drive_video(item: dict) -> bool:
    mime = str(item.get("mimeType") or "")
    name = str(item.get("name") or "")
    if mime.startswith("video/"):
        return True
    return Path(name).suffix.lower() in VIDEO_EXT


def _usable_video(item: dict) -> bool:
    if is_junk_name(item.get("name") or ""):
        return False
    if not _is_drive_video(item):
        return False
    size = _int(item.get("size"))
    return size is None or size >= min_video_bytes()


def walk_drive(folder_id: str, depth: int = 0, seen: set[str] | None = None) -> list[dict]:
    """Best-first walk: folders named like clips/raw/extraits first, fonts/music/logo last.

    Only usable videos are returned and only they count towards MAX_FILES.
    """
    seen = seen if seen is not None else set()
    out: list[dict] = []
    seq = 0
    heap: list[tuple[int, int, int, str]] = [(0, depth, seq, folder_id)]
    calls = 0
    while heap and len(out) < MAX_FILES and calls < MAX_FOLDER_CALLS:
        _neg_prio, d, _s, fid = heapq.heappop(heap)
        if not fid or fid in seen or d > MAX_DEPTH:
            continue
        seen.add(fid)
        calls += 1
        rows = gog_ls(fid)
        for item in rows:
            mime = str(item.get("mimeType") or "")
            iid = str(item.get("id") or "")
            name = str(item.get("name") or "")
            target_folder = None
            if mime == FOLDER_MIME:
                target_folder = iid
            elif mime == SHORTCUT_MIME:
                details = item.get("shortcutDetails") or {}
                tid = details.get("targetId")
                tmime = details.get("targetMimeType") or ""
                if tmime == FOLDER_MIME:
                    target_folder = str(tid or "")
                elif tid:
                    item = dict(item)
                    item["id"] = tid
                    item["mimeType"] = tmime or mime
            if target_folder is not None:
                if d < MAX_DEPTH and not is_junk_name(name):
                    seq += 1
                    heapq.heappush(heap, (-folder_priority(name), d + 1, seq, target_folder))
                continue
            if _usable_video(item) and len(out) < MAX_FILES:
                out.append(item)
    return out


def _resolved(
    *,
    source_url: str,
    source_id: str,
    provider: str,
    name: str,
    kind: str,
    size: int | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "source_url": source_url,
        "source_id": source_id,
        "source_provider": provider,
        "name": name,
        "kind": kind,
    }
    if size:
        out["size"] = size
    return out


# --------------------------------------------------------------------------
# Streamer channels (YouTube / Twitch / Kick) -> latest VODs, metadata only.
# --------------------------------------------------------------------------


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def channel_settings() -> dict[str, Any]:
    """All knobs by env. Nothing is downloaded: only metadata is read."""
    vods = max(0, _env_int("RESOLVE_CHANNEL_VODS", 2))
    return {
        "enabled": os.environ.get("RESOLVE_CHANNELS_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"},
        "vods": vods,
        "max_age_days": max(0, _env_int("RESOLVE_CHANNEL_MAX_AGE_DAYS", 7)),
        "max_duration_s": max(0, _env_int("RESOLVE_CHANNEL_MAX_DURATION_S", 4 * 3600)),
        "min_duration_s": max(0, _env_int("RESOLVE_CHANNEL_MIN_DURATION_S", 60)),
        # How many newest entries to inspect to find `vods` that pass the filters.
        "scan": max(1, min(_env_int("RESOLVE_CHANNEL_SCAN", max(vods * 3, 1)), 20)),
        "ytdlp_bin": os.environ.get("RESOLVE_YTDLP_BIN", "yt-dlp"),
        "timeout_s": max(10, _env_int("RESOLVE_YTDLP_TIMEOUT_S", 180)),
    }


def channel_ref(url: str) -> tuple[str, str] | None:
    """(platform, listing_url) for a channel URL, else None.

    YouTube /@x, /channel/ID, /c/x, /user/x (+ /videos|/streams), twitch.tv/x
    (+ /videos) and kick.com/x (+ /videos). Disabled -> None.
    """
    if not channel_settings()["enabled"]:
        return None
    u = (url or "").strip()
    try:
        host = (urlparse(u).hostname or "").lower()
    except ValueError:
        return None
    if host.endswith("youtube.com"):
        m = YT_CHANNEL_RE.search(u)
        if not m:
            return None
        tab = (m.group(2) or "videos").lower()
        tab = "streams" if tab == "streams" else "videos"
        return "youtube", f"https://www.youtube.com/{m.group(1)}/{tab}"
    if host == "twitch.tv" or host.endswith(".twitch.tv"):
        m = TWITCH_CHANNEL_RE.search(u)
        if not m or m.group(1).lower() in _CHANNEL_RESERVED:
            return None
        return "twitch", f"https://www.twitch.tv/{m.group(1)}/videos?filter=archives&sort=time"
    if host == "kick.com" or host.endswith(".kick.com"):
        m = KICK_CHANNEL_RE.search(u)
        if not m or m.group(1).lower() in _CHANNEL_RESERVED:
            return None
        return "kick", m.group(1)
    return None


def _run_ytdlp_json(listing_url: str, cfg: dict[str, Any]) -> dict:
    """yt-dlp -J over the newest `scan` entries, without downloading media."""
    cmd = [
        cfg["ytdlp_bin"], "-J", "--skip-download", "--ignore-errors", "--no-warnings",
        "--playlist-items", f"1:{cfg['scan']}", listing_url,
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=cfg["timeout_s"])
    out = (p.stdout or "").strip()
    if not out:
        raise RuntimeError((p.stderr or f"yt-dlp exit {p.returncode}")[-300:])
    return json.loads(out)


def _kick_api_videos(slug: str, cfg: dict[str, Any]) -> list[dict]:
    """Kick has no channel-listing extractor in yt-dlp: read the public JSON
    listing and map it to yt-dlp-like entries. Each VOD URL is then a
    kick.com/<slug>/videos/<uuid> that yt-dlp (kick:vod) downloads."""
    req = urllib.request.Request(
        f"https://kick.com/api/v2/channels/{slug}/videos",
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) clipping-system-vps/1.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=cfg["timeout_s"]) as r:  # noqa: S310 (public https)
        data = json.loads(r.read().decode("utf-8", errors="replace"))
    entries = []
    for v in data if isinstance(data, list) else []:
        if not isinstance(v, dict) or v.get("is_live"):
            continue
        vid = (v.get("video") or {}).get("uuid")
        if not vid:
            continue
        dur = v.get("duration")
        entries.append({
            "id": vid,
            "title": v.get("session_title") or vid,
            "duration": (float(dur) / 1000.0) if isinstance(dur, (int, float)) else None,
            "release_date_str": v.get("start_time") or v.get("created_at"),
            "webpage_url": f"https://kick.com/{slug}/videos/{vid}",
            "live_status": "was_live",
        })
    return entries


def _entry_time(e: dict) -> datetime | None:
    for key in ("timestamp", "release_timestamp"):
        v = e.get(key)
        if isinstance(v, (int, float)) and v > 0:
            return datetime.fromtimestamp(float(v), tz=timezone.utc)
    for key in ("upload_date", "release_date"):
        v = str(e.get(key) or "")
        if len(v) == 8 and v.isdigit():
            return datetime.strptime(v, "%Y%m%d").replace(tzinfo=timezone.utc)
    v = e.get("release_date_str")
    if v:
        try:
            return datetime.strptime(str(v)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def pick_vods(entries: list[dict], cfg: dict[str, Any], *, now: datetime | None = None) -> list[dict]:
    """Newest-first entries -> up to cfg['vods'] that are finished, recent and
    within the duration window. Unknown date or duration is skipped (safer)."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=cfg["max_age_days"])
    out: list[dict] = []
    for e in entries:
        if len(out) >= cfg["vods"]:
            break
        if not isinstance(e, dict):
            continue
        if e.get("live_status") in {"is_live", "is_upcoming", "post_live"}:
            continue
        when = _entry_time(e)
        dur = e.get("duration")
        if when is None or when < cutoff:
            continue
        if not isinstance(dur, (int, float)):
            continue
        if dur < cfg["min_duration_s"] or (cfg["max_duration_s"] and dur > cfg["max_duration_s"]):
            continue
        out.append(e)
    return out


def resolve_channel_vods(url: str, *, now: datetime | None = None) -> tuple[list[dict], str | None]:
    """Channel URL -> (assets for its latest VODs, error kind or None)."""
    ref = channel_ref(url)
    if not ref:
        return [], "unsupported_source"
    platform, listing = ref
    cfg = channel_settings()
    if cfg["vods"] <= 0:
        return [], "channel_disabled"
    try:
        if platform == "kick":
            entries = _kick_api_videos(listing, cfg)
        else:
            data = _run_ytdlp_json(listing, cfg)
            entries = [e for e in (data.get("entries") or []) if isinstance(e, dict)]
    except Exception as e:  # noqa: BLE001 - network/yt-dlp failure, reported, not raised
        return [], f"channel_listing_failed:{type(e).__name__}"
    picked = pick_vods(entries, cfg, now=now)
    out: list[dict] = []
    for e in picked:
        vid = str(e.get("id") or "")
        if platform == "twitch":
            vid = vid.lstrip("v")  # yt-dlp twitch ids are "v<digits>"
        page = e.get("webpage_url") or e.get("url") or ""
        if platform == "twitch" and vid and not page.startswith("http"):
            page = f"https://www.twitch.tv/videos/{vid.lstrip('v')}"
        if platform == "youtube" and vid and not page.startswith("http"):
            page = f"https://www.youtube.com/watch?v={vid}"
        if not vid or not page:
            continue
        out.append(_resolved(
            source_url=page,
            source_id=f"{platform}:{vid}",
            provider=platform if platform == "youtube" else f"{platform}_vod",
            name=str(e.get("title") or vid)[:200],
            kind="channel_vod",
        ))
    return out, (None if out else "channel_no_recent_vods")


def expand_url(url: str) -> tuple[list[dict], str | None]:
    """Return (assets, error_kind_or_none). error only if this URL should fail the campaign when it is the only source."""
    url = normalize_url(url)
    kind = classify(url)
    if kind in {"brief_doc", "profile", "invalid", "social", "whop_other"}:
        return [], None
    if kind == "whop_video":
        ok, size, ctype = head_check(url)
        if not ok:
            return [], "whop_asset_unreachable"
        if size is not None and size < min_video_bytes():
            return [], None
        if ctype and not (ctype.startswith("video/") or "octet-stream" in ctype):
            return [], None
        key = urlparse(url).path.lstrip("/")
        return [_resolved(
            source_url=url,
            source_id=key,
            provider="direct",
            name=Path(key).name,
            kind="direct",
            size=size,
        )], None
    if kind == "unsupported":
        return [], "unsupported_source"
    if kind == "unknown":
        return [], "unsupported_source"
    if kind == "drive_folder":
        fid = drive_folder_id(url)
        if not fid:
            return [], "unsupported_source"
        try:
            items = walk_drive(fid)
        except Exception as e:
            return [], f"gog:{e}"
        out = []
        for item in items:
            if not _usable_video(item):
                continue
            iid = str(item.get("id") or "")
            name = str(item.get("name") or iid)
            if not iid:
                continue
            out.append(_resolved(
                source_url=f"https://drive.google.com/uc?export=download&id={iid}",
                source_id=iid,
                provider="gdrive",
                name=name,
                kind="video",
                size=_int(item.get("size")),
            ))
            if len(out) >= MAX_FILES:
                break
        return out, (None if out else "empty_drive_folder")
    if kind == "drive_file":
        iid = drive_file_id(url) or url
        return [_resolved(
            source_url=f"https://drive.google.com/uc?export=download&id={iid}" if drive_file_id(url) else url,
            source_id=str(iid),
            provider="gdrive",
            name=str(iid),
            kind="video",
        )], None
    if kind == "dropbox_file":
        dl = url if "dl=1" in url else (url + ("&dl=1" if "?" in url else "?dl=1"))
        mid = None
        m = DROPBOX_FILE_RE.search(url)
        if m:
            mid = m.group(1)
        return [_resolved(
            source_url=dl,
            source_id=mid or dl[-80:],
            provider="dropbox",
            name=Path(urlparse(url).path).name or "dropbox-file",
            kind="video",
        )], None
    if kind == "dropbox_folder":
        return [], "dropbox_folder_needs_list"
    if kind == "channel":
        return resolve_channel_vods(url)
    if kind in {"youtube", "tiktok", "vimeo", "direct", "twitch_vod", "kick_vod"}:
        ident = url
        m = YT_RE.search(url) or TIKTOK_RE.search(url) or VIMEO_RE.search(url) or TWITCH_VOD_RE.search(url)
        if m:
            ident = m.group(1)
        km = KICK_VOD_RE.search(url)
        if kind == "kick_vod" and km:
            ident = km.group(2)
        return [_resolved(
            source_url=url,
            source_id=ident,
            provider=kind,
            name=ident,
            kind=kind,
        )], None
    return [], "unsupported_source"


def expand_all(urls: list[str]) -> tuple[list[dict], list[str]]:
    found: list[dict] = []
    seen: set[str] = set()
    errors: list[str] = []
    had_ingest_attempt = False
    social_seen = False
    for url in urls:
        url = normalize_url(url)
        kind = classify(url)
        if kind in {"brief_doc", "profile", "invalid", "social", "whop_other"}:
            social_seen = social_seen or kind in {"social", "profile"}
            continue
        assets, err = expand_url(url)
        had_ingest_attempt = True
        if err and err.startswith("gog:"):
            errors.append(err)
        elif err == "dropbox_folder_needs_list":
            errors.append("dropbox_folder_needs_list")
        elif err == "unsupported_source":
            errors.append(f"unsupported_source:{url[:120]}")
        elif err == "whop_asset_unreachable":
            errors.append(f"whop_asset_unreachable:{url[-80:]}")
        elif err and err.startswith("channel_"):
            errors.append(f"{err}:{url[:120]}")
        for a in assets:
            key = a["source_id"] or a["source_url"]
            if key in seen:
                continue
            seen.add(key)
            found.append(a)
            if len(found) >= MAX_FILES:
                return found, errors
    if not had_ingest_attempt and not found:
        errors.append("social_only" if social_seen else "no_ingestible_urls")
    return found, errors
