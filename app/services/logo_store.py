"""Campaign logos stored on the server as PNG (#55).

The API is Tailscale-only. ``rules_overrides.logo_url`` points at
``GET /worker/campaigns/{id}/logo``, which the worker fetches with its token.
PNG bytes pass through. JPEG/WebP go through Pillow. SVG needs cairosvg
and libcairo; without them the upload is rejected and PNG still works.
"""
from __future__ import annotations

import io
from pathlib import Path

from app.config import settings

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MAX_LOGO_BYTES = 5 * 1024 * 1024


class LogoError(ValueError):
    """The upload is not a logo we can store as PNG."""


def logo_dir() -> Path:
    raw = (settings.logo_dir or "").strip()
    if raw:
        return Path(raw)
    if Path("/opt/clipping-system/storage").is_dir():
        return Path("/opt/clipping-system/storage/logos")
    return Path("storage/logos")


def logo_file_path(campaign_id: int) -> Path:
    return logo_dir() / f"{int(campaign_id)}.png"


def rasterize_logo(data: bytes, content_type: str = "", filename: str = "") -> bytes:
    if not data:
        raise LogoError("empty logo")
    if len(data) > MAX_LOGO_BYTES:
        raise LogoError("logo larger than 5MB")
    if data.startswith(PNG_MAGIC):
        return data
    ctype = (content_type or "").lower()
    name = (filename or "").lower()
    head = data[:400].lstrip().lower()
    if "svg" in ctype or name.endswith(".svg") or head.startswith(b"<svg") or head.startswith(b"<?xml"):
        return _svg_to_png(data)
    return _pillow_to_png(data)


def _svg_to_png(data: bytes) -> bytes:
    try:
        import cairosvg
    except Exception as e:
        raise LogoError("SVG needs cairosvg and libcairo2; upload a PNG instead") from e
    try:
        out = cairosvg.svg2png(bytestring=data)
    except Exception as e:
        raise LogoError(f"could not convert SVG: {e}") from e
    if not out or not bytes(out).startswith(PNG_MAGIC):
        raise LogoError("SVG conversion did not produce a PNG")
    return bytes(out)


def _pillow_to_png(data: bytes) -> bytes:
    try:
        from PIL import Image
    except Exception as e:
        raise LogoError("raster conversion needs Pillow") from e
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGBA")
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        out = buf.getvalue()
    except Exception as e:
        raise LogoError(f"unsupported logo image: {e}") from e
    if not out.startswith(PNG_MAGIC):
        raise LogoError("conversion did not produce a PNG")
    return out
