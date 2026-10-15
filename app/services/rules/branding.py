"""Brand templates (MOLI-54) — resolution + storage on the VPS.

A campaign picks a template from ``rules_overrides.branding`` (human-managed,
up to MAX_TEMPLATES = 4). Stored pieces:

* ``templates`` — list of ``{name, font_name, caption_color, outline_color,
  position, max_words}``; the campaign's ``template`` names one of them.
* ``font``      — one font file (TTF/OTF) served at
  ``/worker/campaigns/{id}/fonts/<name>`` via ``font_url``.
* ``intro`` / ``outro`` — image (png/jpg/webp) or short video (mp4/webm/mov)
  served at ``/worker/campaigns/{id}/branding/{intro|outro}``.

Everything optional: a campaign without branding gets the previous
hard-coded worker behaviour unchanged.
"""
from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

from app.config import settings

logger = logging.getLogger("branding")

MAX_TEMPLATES = 4
MAX_FONT_BYTES = 10 * 1024 * 1024
MAX_MEDIA_BYTES = 50 * 1024 * 1024

FONT_EXTENSIONS = (".ttf", ".otf")
MEDIA_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm", ".mov")

_TTF_MAGIC = b"\x00\x01\x00\x00"  # TrueType
_OTF_MAGIC = b"OTTO"              # OpenType with CFF
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPG_MAGIC = b"\xff\xd8"
_WEBP_MAGIC = b"RIFF"
_MP4_MAGIC = b"ftyp"
_WEBM_MAGIC = b"\x1aE\xdf\xa3"
_MOV_MAGIC = b"ftyp"

_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")


class BrandingError(ValueError):
    """The upload is not an asset we can store (bad type, too big, bad name)."""


def branding_dir() -> Path:
    raw = (settings.branding_dir or "").strip()
    if raw:
        return Path(raw)
    if Path("/opt/clipping-system/storage").is_dir():
        return Path("/opt/clipping-system/storage/branding")
    return Path("storage/branding")


def campaign_branding_dir(campaign_id: int) -> Path:
    path = branding_dir() / str(int(campaign_id))
    path.mkdir(parents=True, exist_ok=True)
    return path


def templates(campaign) -> list[dict]:
    """Templates list from the campaign overrides (max 4, newest wins)."""
    meta = getattr(campaign, "source_metadata", None) or {}
    overrides = meta.get("rules_overrides") if isinstance(meta.get("rules_overrides"), dict) else {}
    raw = overrides.get("branding") if isinstance(overrides, dict) else None
    if isinstance(raw, list):
        return [t for t in raw if isinstance(t, dict)][:MAX_TEMPLATES]
    if isinstance(raw, dict):
        return [raw][:MAX_TEMPLATES]
    return []


def resolve(campaign, rs=None) -> dict | None:
    """Effective branding for the campaign, or None when absent.

    Reads ``rules_overrides.branding`` first (a dict = single template; the
    schema default). ``rs.branding`` (RuleSet field) only fills the gap when
    the override says nothing at all. Stored files (font/intro/outro on disk)
    are exposed even without any template config — uploading a file is
    itself the configuration. Never merges partial overrides: a template is
    one complete block.
    """
    raw = (getattr(campaign, "source_metadata", None) or {}).get("rules_overrides") or {}
    if isinstance(raw, dict):
        b = raw.get("branding")
        if isinstance(b, dict) and b:
            return _complete(b, campaign)
        if isinstance(b, list) and b:
            # list of templates: use the one named by `template` (or the first)
            name = (rs.branding.template if rs is not None else None) or (b[0] or {}).get("name") if isinstance(b[0], dict) else None
            for t in b:
                if isinstance(t, dict) and (name is None or t.get("name") == name):
                    return _complete(t, campaign)
    configured = None
    if rs is not None:
        br = rs.branding
        if (br.font_url or br.intro.url or br.outro.url
                or br.caption_color or br.font_name):
            configured = br.model_dump(exclude_none=True)
    cid = getattr(campaign, "id", None)
    if configured is None and cid is not None:
        # No config at all: expose stored files (upload = configuration).
        pieces: dict = {}
        font = font_file_path(cid)
        if font is not None:
            pieces["font_url"] = f"/worker/campaigns/{cid}/fonts/{font.name}"
            pieces["font_name"] = font.stem or "Custom"
        for side in ("intro", "outro"):
            media = media_file_path(cid, side)
            if media is not None:
                pieces[side] = {"url": f"/worker/campaigns/{cid}/branding/{side}"}
        return pieces or None
    if configured:
        return _complete(configured, campaign) or None
    return None


def _complete(block: dict, campaign) -> dict:
    """Fill the serving URLs from stored files so the worker needs no path logic."""
    cid = getattr(campaign, "id", None)
    out = dict(block or {})
    if cid is not None:
        font = font_file_path(cid)
        if font is not None and not out.get("font_url"):
            out["font_url"] = f"/worker/campaigns/{cid}/fonts/{font.name}"
        for side in ("intro", "outro"):
            media = media_file_path(cid, side)
            if media is not None and not (out.get(side) or {}).get("url"):
                d = dict(out.get(side) or {})
                d["url"] = f"/worker/campaigns/{cid}/branding/{side}"
                out[side] = d
    return {k: v for k, v in out.items() if v not in (None, {}, [])}


def set_template(campaign, patch: dict) -> list[dict]:
    """Add or replace one template in rules_overrides.branding (returns all)."""
    name = str((patch or {}).get("name") or "").strip()
    if not name or not _NAME.fullmatch(name):
        raise BrandingError("template.name must match [A-Za-z0-9_-]{1,64}")
    meta = dict(campaign.source_metadata or {})
    overrides = dict(meta.get("rules_overrides") or {})
    current = overrides.get("branding")
    items = [t for t in (current if isinstance(current, list) else [current] if isinstance(current, dict) else []) if isinstance(t, dict)]
    items = [t for t in items if t.get("name") != name]
    items.append(patch)
    if len(items) > MAX_TEMPLATES:
        raise BrandingError(f"at most {MAX_TEMPLATES} templates; replace one first")
    overrides["branding"] = items
    meta["rules_overrides"] = overrides
    campaign.source_metadata = meta
    return items


def font_file_path(campaign_id: int) -> Path | None:
    """The stored font (any TTF/OTF inside the campaign folder), if any."""
    d = campaign_branding_dir(campaign_id)
    for f in d.iterdir():
        if f.is_file() and f.suffix.lower() in FONT_EXTENSIONS:
            return f
    return None


def media_file_path(campaign_id: int, side: str) -> Path | None:
    """Stored intro/outro (image or video), if any. side ∈ {intro, outro}."""
    if side not in ("intro", "outro"):
        return None
    d = campaign_branding_dir(campaign_id)
    for f in d.iterdir():
        if f.is_file() and f.name == side or f.is_file() and f.name.startswith(f"{side}."):
            if f.suffix.lower() in MEDIA_EXTENSIONS:
                return f
    return None


def _magic_matches(data: bytes, ext: str) -> bool:
    ext = ext.lower()
    if ext == ".png":
        return data.startswith(_PNG_MAGIC)
    if ext in (".jpg", ".jpeg"):
        return data.startswith(_JPG_MAGIC)
    if ext == ".webp":
        return data.startswith(_WEBP_MAGIC) and data[8:12] == b"WEBP"
    if ext in (".mp4", ".mov"):
        return data[4:8] == _MP4_MAGIC
    if ext == ".webm":
        return data.startswith(_WEBM_MAGIC)
    if ext in (".ttf",):
        head = data[:4]
        return head == _TTF_MAGIC or head == b"true" or head == b"ttcf"
    if ext == ".otf":
        return data.startswith(_OTF_MAGIC)
    return False


def store_font(campaign_id: int, data: bytes, filename: str) -> Path:
    """Store a TTF/OTF; replaces any previous font. Returns the path."""
    if not data:
        raise BrandingError("empty font file")
    if len(data) > MAX_FONT_BYTES:
        raise BrandingError("font larger than 10MB")
    name = Path(filename or "").name
    stem = Path(name).stem
    if not _NAME.fullmatch(stem or "") or Path(name).suffix.lower() not in ("",) + FONT_EXTENSIONS:
        raise BrandingError("font filename must match [A-Za-z0-9_-]{1,64}(.ttf|.otf)")
    ext = Path(name).suffix.lower()
    if ext not in FONT_EXTENSIONS:
        raise BrandingError("font must be TTF or OTF")
    if not _magic_matches(data, ext):
        raise BrandingError(f"file is not a real {ext.removeprefix('.').upper()}")
    dest = campaign_branding_dir(campaign_id) / f"font{ext}"
    _atomic_write(dest, data)
    # remove a previous font with the other extension
    for other in FONT_EXTENSIONS:
        if other != ext:
            (dest.parent / f"font{other}").unlink(missing_ok=True)
    return dest


def store_media(campaign_id: int, side: str, data: bytes, filename: str) -> Path:
    """Store an intro/outro image or short video; replaces the previous one."""
    if side not in ("intro", "outro"):
        raise BrandingError("side must be intro or outro")
    if not data:
        raise BrandingError("empty file")
    if len(data) > MAX_MEDIA_BYTES:
        raise BrandingError("file larger than 50MB")
    name = Path(filename or "").name
    stem = Path(name).stem
    if not _NAME.fullmatch(stem or "") or Path(name).suffix.lower() not in ("",) + MEDIA_EXTENSIONS:
        raise BrandingError("filename must match [A-Za-z0-9_-]{1,64}")
    ext = Path(name).suffix.lower()
    if ext not in MEDIA_EXTENSIONS:
        raise BrandingError("media must be png/jpg/webp/mp4/webm/mov")
    if not _magic_matches(data, ext):
        raise BrandingError(f"file is not a real {ext.removeprefix('.').upper()}")
    dest = campaign_branding_dir(campaign_id) / f"{side}{ext}"
    _atomic_write(dest, data)
    for other in MEDIA_EXTENSIONS:
        if other != ext:
            (dest.parent / f"{side}{other}").unlink(missing_ok=True)
    return dest


def save_template(campaign, patch: dict) -> dict:
    """Upsert a template and store it in rules_overrides (returns the list)."""
    items = set_template(campaign, patch)
    meta = dict(campaign.source_metadata or {})
    overrides = dict(meta.get("rules_overrides") or {})
    overrides["branding"] = items
    meta["rules_overrides"] = overrides
    campaign.source_metadata = meta
    return items


def clear_piece(campaign_id: int, side: str) -> None:
    """Remove the stored file for intro/outro/font (keeps the DB state)."""
    d = campaign_branding_dir(campaign_id)
    if side == "font":
        for f in d.glob("font.*"):
            f.unlink(missing_ok=True)
        return
    for f in d.glob(f"{side}.*"):
        f.unlink(missing_ok=True)


def _atomic_write(dest: Path, data: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.tmp")
    tmp.write_bytes(data)
    tmp.replace(dest)