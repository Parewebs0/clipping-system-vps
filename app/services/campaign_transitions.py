"""Manual campaign status transitions (issue #9).

The pipeline v2 ticks own the *forward* transitions:

    discovered --3a--> briefed | failed_brief
    briefed / failed_resolve(gog) --3b--> assets_resolved | failed_resolve
    assets_resolved --3c--> scored | blocked_no_assets
    scored --7--> (download jobs enqueued)

A human (Mission Control / API) may only:
  * send a campaign *back* to an earlier status so the matching tick picks it
    up again on its next run (retry a step), or
  * park it (scored -> blocked_no_assets stops download_enqueue_tick), or
  * archive / un-archive it.

Forward jumps (e.g. discovered -> scored) are never allowed: they would make
download_enqueue_tick enqueue downloads for a campaign whose brief/assets were
never processed. This module is the single source of truth; the API exposes it
at GET /campaigns/status-machine so the dashboard shows only valid actions.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.models.campaign import Campaign, CampaignStatus as S

ARCHIVED = S.ARCHIVED.value

# from -> allowed manual targets
MANUAL_TRANSITIONS: dict[str, tuple[str, ...]] = {
    S.DISCOVERED.value: (ARCHIVED,),
    S.BRIEFED.value: (S.DISCOVERED.value, ARCHIVED),
    S.ASSETS_RESOLVED.value: (S.BRIEFED.value, S.DISCOVERED.value, ARCHIVED),
    S.SCORED.value: (S.ASSETS_RESOLVED.value, S.BLOCKED_NO_ASSETS.value, ARCHIVED),
    S.BLOCKED_NO_ASSETS.value: (S.ASSETS_RESOLVED.value, S.BRIEFED.value, ARCHIVED),
    S.FAILED_BRIEF.value: (S.DISCOVERED.value, ARCHIVED),
    S.FAILED_RESOLVE.value: (S.BRIEFED.value, S.DISCOVERED.value, ARCHIVED),
    ARCHIVED: (S.DISCOVERED.value,),
}

# Which tick consumes each status (shown in the UI / docs).
CONSUMED_BY: dict[str, Optional[str]] = {
    S.DISCOVERED.value: "brief_reader_tick (3a)",
    S.BRIEFED.value: "drive_resolver_tick (3b)",
    S.ASSETS_RESOLVED.value: "campaign_scorer_tick (3c)",
    S.SCORED.value: "download_enqueue_tick (7)",
    S.BLOCKED_NO_ASSETS.value: None,
    S.FAILED_BRIEF.value: None,
    S.FAILED_RESOLVE.value: "drive_resolver_tick (3b, solo kind=gog)",
    ARCHIVED: None,
}

# What happens next when a human moves a campaign *to* this status.
EFFECT: dict[str, str] = {
    S.DISCOVERED.value: "El brief-reader (3a) volverá a leer el brief en su próximo tick (llamada LLM).",
    S.BRIEFED.value: "El resolver (3b) volverá a resolver los enlaces y crear assets.",
    S.ASSETS_RESOLVED.value: "El scorer (3c) recalculará el score con los assets actuales.",
    S.BLOCKED_NO_ASSETS.value: "Aparcada: download_enqueue_tick deja de encolar descargas nuevas (los jobs ya encolados siguen).",
    ARCHIVED: "Sale del pipeline: ningún tick la procesa y no cuenta para el límite de campañas activas de discovery. Los jobs ya encolados no se cancelan.",
}

HISTORY_MAX = 50


class TransitionError(ValueError):
    """Raised when a manual transition is not allowed."""


def allowed_targets(current: str) -> tuple[str, ...]:
    return MANUAL_TRANSITIONS.get(current, ())


def apply_transition(campaign: Campaign, target: str, reason: Optional[str] = None, actor: str = "api") -> bool:
    """Validate and apply `campaign.status = target` (no commit).

    Returns False if it is a no-op (already in `target`). Raises
    TransitionError if `target` is unknown or not reachable manually.
    Appends an entry to source_metadata.status_history (last 50).
    """
    current = campaign.status
    if target == current:
        return False
    if target not in MANUAL_TRANSITIONS:
        raise TransitionError(f"Unknown status '{target}'")
    if target not in allowed_targets(current):
        allowed = ", ".join(allowed_targets(current)) or "none"
        raise TransitionError(
            f"Transition '{current}' -> '{target}' is not allowed manually "
            f"(allowed from '{current}': {allowed}). Forward steps are done by the pipeline ticks."
        )
    meta = dict(campaign.source_metadata or {})
    history = list(meta.get("status_history") or [])
    history.append(
        {
            "from": current,
            "to": target,
            "at": datetime.now(timezone.utc).isoformat(),
            "by": actor,
            "reason": (reason or "")[:500] or None,
        }
    )
    meta["status_history"] = history[-HISTORY_MAX:]
    campaign.source_metadata = meta
    campaign.status = target
    return True
