"""Read → extract → enforce → persist (#33). Shared by brief_reader_tick and rules_reader."""
from __future__ import annotations

from typing import Callable, Optional

from app.services.brief_materials import classify_url, extract_urls
from app.services.rules.enforcement import assign_enforcement, blocking_items, legacy_rules
from app.services.rules.extract import extract_ruleset
from app.services.rules.schema import RuleSet
from app.services.rules.sources import SourceBundle, fetch_google_doc, gather_sources

_CONTENT_KINDS = {"drive_folder", "drive_file", "mediasilo", "unsupported_host", "youtube"}


def ensure_detail(campaign, client=None) -> bool:
    """Fetch the public detail if the campaign has none (whop only)."""
    meta = dict(campaign.source_metadata or {})
    if meta.get("detail") or campaign.source_provider != "whop":
        return False
    from app.services.campaign_closed import campaign_uuid_from_url
    from app.services.discovery.whop_catalog import CatalogClient, CatalogFetchError, detail_summary

    uid = campaign_uuid_from_url(campaign.source_url)
    if not uid:
        return False
    try:
        detail = (client or CatalogClient()).fetch_detail(uid)
    except CatalogFetchError:
        return False
    meta["detail"] = detail_summary(detail)
    campaign.source_metadata = meta
    return True


def read_rules(campaign, llm: Callable[..., dict], *, fetch_doc=fetch_google_doc) -> tuple[RuleSet, SourceBundle]:
    bundle = gather_sources(campaign, fetch_doc=fetch_doc)
    rs = extract_ruleset(bundle, llm, campaign_id=campaign.id)
    rs = assign_enforcement(rs)
    return rs, bundle


def persist_rules(campaign, rs: RuleSet, bundle: SourceBundle) -> dict:
    """Store the RuleSet + legacy view + spec. Returns the legacy rules dict."""
    meta = dict(campaign.source_metadata or {})
    all_urls = list(bundle.reference_urls)
    for d in bundle.docs:
        all_urls += extract_urls(d.get("text") or "")
    all_urls += extract_urls(bundle.guidelines)
    content_urls = [u for u in rs.content_source_urls]
    for u in all_urls:
        if classify_url(u) in _CONTENT_KINDS and u not in content_urls:
            content_urls.append(u)
    rs.content_source_urls = content_urls[:40]
    legacy = legacy_rules(rs)
    legacy["content_source_urls"] = rs.content_source_urls
    legacy["content_source_kinds"] = sorted({classify_url(u) for u in rs.content_source_urls})
    unsupported_host = any(k in {"mediasilo", "unsupported_host"} for k in legacy["content_source_kinds"]) and not any(
        k in {"drive_folder", "drive_file", "youtube"} for k in legacy["content_source_kinds"])
    legacy["unsupported_video_host"] = unsupported_host
    if unsupported_host:
        legacy["heavy_source_files"] = True
    meta["ruleset"] = rs.dump()
    meta["rules"] = legacy
    meta["asset_links"] = rs.content_source_urls
    meta["brief_docs"] = rs.meta.get("docs") or []
    meta["rules_blockers"] = blocking_items(rs)
    campaign.source_metadata = meta
    from app.services.rules.gate import reconcile_confirmations

    reconcile_confirmations(campaign, rs)
    spec = dict(campaign.spec or {})
    spec["duration_min"] = float(rs.duration.min_s)
    spec["duration_max"] = float(rs.duration.max_s)
    spec["format"] = rs.aspect.ratio or "9:16"
    spec["language"] = rs.language.code
    extra = dict(spec.get("extra") or {})
    extra["brief_rules"] = legacy
    extra["ruleset_version"] = rs.version
    spec["extra"] = extra
    campaign.spec = spec
    campaign.source_instructions = (legacy.get("extra_rules") or legacy.get("difficulty_notes") or "")[:4000]
    return legacy


def ruleset_of(campaign) -> Optional[RuleSet]:
    from app.services.rules.schema import load_ruleset
    return load_ruleset(campaign.source_metadata or {})
