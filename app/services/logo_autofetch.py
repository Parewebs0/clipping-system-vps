"""Download a Whop organization logo into the existing logo store (#55).

Called from ``upsert_campaign`` after the row is saved. Never raises and
never confirms ``human:logo_file``: the organization mark may not be the
brand logo, so Mission Control still confirms it.

Manual uploads win. ``rules_overrides.logo_source`` is ``manual`` after
``POST /campaigns/{id}/rules/logo`` and ``whop_org`` after this fetch.
A logo URL already set by hand (no source, or source other than
``whop_org``) is left untouched. The same Whop URL is not downloaded twice.
"""
from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx

from app.services.asset_resolve import normalize_url
from app.services.logo_store import MAX_LOGO_BYTES, LogoError, rasterize_logo, store_logo_png
from app.services.rules.enforcement import blocking_items
from app.services.rules.schema import load_ruleset

logger = logging.getLogger(__name__)

LOGO_SOURCE_MANUAL = "manual"
LOGO_SOURCE_WHOP = "whop_org"
FETCH_TIMEOUT_S = 15.0
_UA = "clipping-system-vps/1.0 (logo-fetch)"


class LogoFetchError(Exception):
    """The bytes never arrived. ``reason`` is a stable code, not a URL."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def worker_logo_path(campaign_id: int) -> str:
    return f"/worker/campaigns/{int(campaign_id)}/logo"


def apply_org_logo(campaign) -> bool:
    """Store the org logo when this campaign needs a file and nobody uploaded one.

    Returns True when ``source_metadata`` changed and the caller should commit.
    Download failures are logged with ``campaign_id`` and a reason code.
    """
    try:
        return _apply_org_logo(campaign)
    except Exception:  # noqa: BLE001 — a logo must never break campaign save
        logger.warning(
            "logo autofetch failed campaign_id=%s reason=unexpected",
            getattr(campaign, "id", None),
        )
        return False


def _apply_org_logo(campaign) -> bool:
    campaign_id = getattr(campaign, "id", None)
    if not isinstance(campaign_id, int):
        return False
    meta = dict(getattr(campaign, "source_metadata", None) or {})
    if not _needs_logo_file(meta):
        return False
    raw_overrides = meta.get("rules_overrides")
    overrides = dict(raw_overrides) if isinstance(raw_overrides, dict) else {}
    source = str(overrides.get("logo_source") or "").strip().lower()
    if source == LOGO_SOURCE_MANUAL:
        return False
    # A URL set by the upload endpoint before logo_source existed, or by hand.
    if overrides.get("logo_url") and source != LOGO_SOURCE_WHOP:
        return False
    raw_url = _organization_logo_url(meta)
    if not raw_url:
        logger.warning(
            "logo autofetch skipped campaign_id=%s reason=no_organization_logo_url",
            campaign_id,
        )
        return False
    if source == LOGO_SOURCE_WHOP and str(overrides.get("logo_whop_url") or "") == raw_url:
        return False
    absolute = normalize_url(raw_url)
    if not absolute.lower().startswith(("http://", "https://")):
        logger.warning(
            "logo autofetch failed campaign_id=%s reason=unsupported_url",
            campaign_id,
        )
        return False
    try:
        data, content_type = _download(absolute)
        png = rasterize_logo(data, content_type, urlparse(absolute).path)
        store_logo_png(campaign_id, png)
    except LogoFetchError as exc:
        logger.warning(
            "logo autofetch failed campaign_id=%s reason=%s",
            campaign_id,
            exc.reason,
        )
        return False
    except LogoError:
        logger.warning(
            "logo autofetch failed campaign_id=%s reason=unsupported_format",
            campaign_id,
        )
        return False
    overrides["logo_url"] = worker_logo_path(campaign_id)
    overrides["logo_source"] = LOGO_SOURCE_WHOP
    overrides["logo_whop_url"] = raw_url
    meta["rules_overrides"] = overrides
    campaign.source_metadata = meta
    logger.info("logo autofetch stored campaign_id=%s source=%s", campaign_id, LOGO_SOURCE_WHOP)
    return True


def _needs_logo_file(meta: dict) -> bool:
    """Logo is required and the ruleset does not already point at an image.

    That is the ``human:logo_file`` blocker, and also a required logo whose
    enforcement has not been assigned yet. An image URL already on the
    ruleset stays the brand file: we do not replace it with the org mark.
    """
    rs = load_ruleset(meta)
    if rs is None or not rs.logo.required:
        return False
    if any(item.get("key") == "human:logo_file" for item in blocking_items(rs)):
        return True
    return not (rs.logo.url or "").strip()


def _organization_logo_url(meta: dict) -> str:
    discovered = meta.get("discovered")
    if not isinstance(discovered, dict):
        return ""
    raw = discovered.get("raw")
    if not isinstance(raw, dict):
        return ""
    value = raw.get("organization_logo_url")
    if not isinstance(value, str):
        return ""
    return value.strip()


def _download(url: str, *, timeout: float = FETCH_TIMEOUT_S) -> tuple[bytes, str]:
    """GET the logo. Raises LogoFetchError. Does not log the URL."""
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": _UA}) as client:
            with client.stream("GET", url) as resp:
                if resp.status_code == 404:
                    raise LogoFetchError("http_404")
                if resp.status_code < 200 or resp.status_code >= 300:
                    raise LogoFetchError(f"http_{resp.status_code}")
                declared = _content_length(resp.headers)
                if declared is not None and declared > MAX_LOGO_BYTES:
                    raise LogoFetchError("larger_than_5mb")
                chunks: list[bytes] = []
                total = 0
                for chunk in resp.iter_bytes():
                    total += len(chunk)
                    if total > MAX_LOGO_BYTES:
                        raise LogoFetchError("larger_than_5mb")
                    chunks.append(chunk)
                content_type = resp.headers.get("content-type") or ""
                return b"".join(chunks), content_type
    except LogoFetchError:
        raise
    except httpx.TimeoutException:
        raise LogoFetchError("timeout") from None
    except httpx.HTTPError:
        raise LogoFetchError("http_error") from None


def _content_length(headers) -> int | None:
    raw = headers.get("content-length")
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None
