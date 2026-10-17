"""Hidden Whop brief detection (Content Rewards "Join to see the brief").

Since Oct 2026 the public detail API returns `referenceMaterials: []` and only
`briefCounts.references` (> 0). Such a campaign reaches the resolver with no
URLs and used to end in failed_resolve/unsupported_source (no_ingestible_urls).
The resolver tick now parks it as `blocked_no_assets` with
resolve_error.kind = NEEDS_JOIN instead, so the reason is explicit and it does
not hold an active slot.
"""
from __future__ import annotations

from typing import Any

NEEDS_JOIN = "needs_join"


def hidden_reference_count(meta: dict[str, Any] | None) -> int:
    """Number of references Whop hides, or 0 when nothing is hidden.

    Hidden = briefCounts.references > 0 while no reference URL reached us
    (asset_links, reference_materials, detail.reference_material, rules
    content_source_urls all empty).
    """
    meta = meta or {}
    detail = meta.get("detail") or {}
    counts = detail.get("brief_counts") or {}
    try:
        refs = int(counts.get("references") or 0)
    except (TypeError, ValueError, AttributeError):
        refs = 0
    if refs <= 0:
        return 0
    discovered = meta.get("discovered") or {}
    rules = meta.get("rules") or {}
    visible = (
        list(meta.get("asset_links") or [])
        + list(meta.get("reference_materials") or [])
        + list(discovered.get("reference_materials") or [])
        + list(discovered.get("asset_links") or [])
        + list(detail.get("reference_material") or [])
        + list(rules.get("content_source_urls") or [])
    )
    return 0 if visible else refs
