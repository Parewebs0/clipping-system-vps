"""Score thresholds for clip candidates (decider --approve).

Rule set by Jesús: score >= 0.80 is approved, < 0.60 is rejected, and
0.60-0.79 stays pending for a person. Scores are on a 0-1 scale.
Only status 'pending' is touched; needs_review and the rest stay as they are.
"""
from __future__ import annotations

import os
from typing import Callable, Iterable

DEFAULT_APPROVE_MIN = 0.80
DEFAULT_REJECT_BELOW = 0.60

APPROVE = "approve"
REJECT = "reject"
KEEP = "keep"


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def default_approve_min() -> float:
    return _env_float("DECIDER_APPROVE_MIN", DEFAULT_APPROVE_MIN)


def default_reject_below() -> float:
    return _env_float("DECIDER_REJECT_BELOW", DEFAULT_REJECT_BELOW)


def validate_thresholds(approve_min: float, reject_below: float) -> None:
    if not (0.0 <= reject_below <= approve_min <= 1.0):
        raise ValueError(
            f"thresholds must satisfy 0 <= reject_below ({reject_below}) "
            f"<= approve_min ({approve_min}) <= 1"
        )


def threshold_action(status, score, approve_min: float, reject_below: float) -> str:
    """approve / reject / keep for one candidate. Only 'pending' moves."""
    if status != "pending":
        return KEEP
    try:
        value = float(score)
    except (TypeError, ValueError):
        return KEEP
    if value >= approve_min:
        return APPROVE
    if value < reject_below:
        return REJECT
    return KEEP


def reject_reason(score, reject_below: float) -> str:
    return f"auto: score {float(score):.2f} < {reject_below:.2f}"


def apply_thresholds(
    db,
    candidates: Iterable,
    *,
    approve_min: float,
    reject_below: float,
    dry_run: bool = False,
    approve: Callable | None = None,
    reject: Callable | None = None,
    log: Callable[[str], None] = print,
) -> dict:
    """Approve/reject pending candidates by score. Returns counts per action."""
    validate_thresholds(approve_min, reject_below)
    if approve is None or reject is None:
        from app.services.candidate_lifecycle import approve_candidate, reject_candidate

        approve = approve or approve_candidate
        reject = reject or reject_candidate
    counts = {APPROVE: 0, REJECT: 0, KEEP: 0}
    for c in candidates:
        action = threshold_action(c.status, c.score, approve_min, reject_below)
        counts[action] += 1
        if action == KEEP:
            continue
        prefix = "[dry-run] " if dry_run else ""
        log(f"{prefix}candidate {c.id} score={c.score} -> {action}")
        if dry_run:
            continue
        if action == APPROVE:
            log(str(approve(db, c.id)))
        else:
            reject(db, c.id, reason=reject_reason(c.score, reject_below))
    return counts
