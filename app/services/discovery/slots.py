"""Which scored campaigns may receive new download jobs (issue #72).

`needs_review` is not a workable status, so it never occupies one of these
slots. The function does not cancel anything: callers only stop enqueueing.
"""
from __future__ import annotations


def _score(campaign) -> float:
    sm = campaign.source_metadata or {}
    score = sm.get("score") if isinstance(sm.get("score"), dict) else {}
    value = score.get("value")
    if value is None:
        value = score.get("total")
    if value is None:
        value = (sm.get("economics") or {}).get("expected_value")
    if value is None:
        value = sm.get("queue_score")
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def rank_for_download(campaigns, limit: int) -> list:
    """Highest score first, then older id. At most `limit` campaigns."""
    ordered = sorted(campaigns, key=lambda c: (-_score(c), c.id))
    if limit < 0:
        limit = 0
    return ordered[:limit]
