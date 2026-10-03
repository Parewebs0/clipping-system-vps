"""#37: workability gate driven by the RuleSet.

A campaign whose RuleSet has a blocking unsupported rule or a human
requirement (account, pre-approval, logo without file) that nobody has
confirmed yet is moved to `needs_review`: nothing is downloaded, rendered or
approved until a human confirms it from the dashboard. Unsupported rules
cannot be confirmed (park / archive the campaign instead).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

from app.services.rules.enforcement import blocking_items, migrate_confirmations
from app.services.rules.schema import load_ruleset

NEEDS_REVIEW = "needs_review"
GATED_FROM = ("scored", "assets_resolved")


class GateError(ValueError):
    """Bad confirmation request (unknown key, unsupported rule…)."""


def confirmations(campaign) -> dict:
    return dict((campaign.source_metadata or {}).get("rules_confirmations") or {})


def blockers(campaign) -> list[dict]:
    """All blocking items with their confirmation (if any)."""
    rs = load_ruleset(campaign.source_metadata or {})
    if rs is None:
        return []
    conf = confirmations(campaign)
    out = []
    for b in blocking_items(rs):
        c = conf.get(b["key"])
        out.append({**b, "confirmed": c is not None, "confirmation": c})
    return out


def pending(campaign) -> list[dict]:
    return [b for b in blockers(campaign) if not b["confirmed"]]


def is_blocked(campaign) -> bool:
    return campaign.status == NEEDS_REVIEW or bool(pending(campaign))


def _reason(items: list[dict]) -> str:
    unsupported = [b for b in items if b["kind"] == "unsupported"]
    human = [b for b in items if b["kind"] == "human"]
    parts = []
    if unsupported:
        parts.append(f"{len(unsupported)} regla(s) no soportada(s): " + "; ".join(b["text"][:80] for b in unsupported[:3]))
    if human:
        parts.append(f"{len(human)} requisito(s) humano(s) sin confirmar: " + "; ".join(b["text"][:60] for b in human[:3]))
    return "rules gate: " + " | ".join(parts)


def apply_gate(campaign, actor: str = "rules_gate") -> bool:
    """Move a workable campaign with pending blockers to needs_review (no commit)."""
    from app.services.campaign_transitions import record_auto_transition

    if campaign.status not in GATED_FROM:
        return False
    items = pending(campaign)
    if not items:
        return False
    prev = campaign.status
    reason = _reason(items)
    record_auto_transition(campaign, NEEDS_REVIEW, reason=reason, actor=actor)
    meta = dict(campaign.source_metadata or {})
    meta["rules_gate"] = {
        "at": datetime.now(timezone.utc).isoformat(),
        "previous_status": prev,
        "pending_keys": [b["key"] for b in items],
        "reason": reason,
    }
    campaign.source_metadata = meta
    return True


def _previous_status(campaign) -> str:
    prev = ((campaign.source_metadata or {}).get("rules_gate") or {}).get("previous_status") or "scored"
    return prev if prev in GATED_FROM else "scored"


def refresh_rules_gate(campaign) -> bool:
    """Rewrite rules_gate reason/pending_keys when a re-read changed the blockers."""
    if campaign.status != NEEDS_REVIEW:
        return False
    items = pending(campaign)
    if not items:
        return False
    meta = dict(campaign.source_metadata or {})
    gate = dict(meta.get("rules_gate") or {})
    reason = _reason(items)
    keys = [b["key"] for b in items]
    if gate.get("reason") == reason and gate.get("pending_keys") == keys:
        return False
    gate["at"] = datetime.now(timezone.utc).isoformat()
    gate["pending_keys"] = keys
    gate["reason"] = reason
    gate.setdefault("previous_status", _previous_status(campaign))
    meta["rules_gate"] = gate
    campaign.source_metadata = meta
    return True


def release_if_clear(campaign, actor: str = "rules_gate") -> bool:
    """needs_review with nothing pending goes back to the status it came from."""
    from app.services.campaign_transitions import record_auto_transition

    if campaign.status != NEEDS_REVIEW or pending(campaign):
        return False
    return record_auto_transition(
        campaign, _previous_status(campaign),
        reason="rules gate: 0 pendientes tras re-lectura", actor=actor,
    )


def record_migration_warnings(campaign, warnings: list[str], actor: str = "rules_migrate") -> None:
    """Append a history note without changing status. Ambiguous migrations stay unconfirmed."""
    if not warnings:
        return
    meta = dict(campaign.source_metadata or {})
    history = list(meta.get("status_history") or [])
    now = datetime.now(timezone.utc).isoformat()
    for warning in warnings:
        history.append({
            "from": campaign.status,
            "to": campaign.status,
            "at": now,
            "by": actor,
            "reason": warning[:500],
        })
    from app.services.campaign_transitions import HISTORY_MAX

    meta["status_history"] = history[-HISTORY_MAX:]
    campaign.source_metadata = meta


def reconcile_confirmations(campaign, rs=None) -> list[str]:
    """Keep confirmations whose key still exists; migrate the rest 1:1 by token overlap.

    Called on --reapply and on every rules persist (an LLM re-read). Returns warnings.
    """
    meta = dict(campaign.source_metadata or {})
    rs = rs or load_ruleset(meta)
    if rs is None:
        return []
    blockers = blocking_items(rs)
    new_conf, warnings = migrate_confirmations(meta.get("rules_confirmations"), blockers)
    if new_conf != dict(meta.get("rules_confirmations") or {}):
        meta["rules_confirmations"] = new_conf
        meta["rules_blockers"] = blockers
        campaign.source_metadata = meta
    record_migration_warnings(campaign, warnings)
    return warnings


def gate_campaigns(db, dry_run: bool = False) -> int:
    from app.models.campaign import Campaign

    n = 0
    dirty = False
    for c in db.query(Campaign).filter(Campaign.status.in_(GATED_FROM)).order_by(Campaign.id):
        items = pending(c)
        if not items:
            continue
        print(f"rules_gate campaign={c.id} status={c.status} pending={len(items)} -> {NEEDS_REVIEW}")
        if dry_run:
            continue
        if apply_gate(c):
            n += 1
            dirty = True
    for c in db.query(Campaign).filter(Campaign.status == NEEDS_REVIEW).order_by(Campaign.id):
        items = pending(c)
        if not items:
            prev = _previous_status(c)
            print(f"rules_gate campaign={c.id} needs_review pending=0 -> {prev}")
            if dry_run:
                continue
            if release_if_clear(c):
                n += 1
                dirty = True
            continue
        if dry_run:
            continue
        if refresh_rules_gate(c):
            print(f"rules_gate campaign={c.id} refreshed pending={len(items)}")
            dirty = True
    if dirty and not dry_run:
        db.commit()
    return n


def confirm(campaign, keys: Iterable[str], note: Optional[str], actor: str = "dashboard",
            waive_unsupported: bool = False) -> dict:
    """Record human confirmations; release the campaign when nothing is pending (no commit).

    Unsupported items can only be *waived* explicitly (`waive_unsupported`)
    and with a note explaining why (#48)."""
    from app.services.campaign_transitions import record_auto_transition

    keys = [k for k in dict.fromkeys(keys or []) if k]
    by_key = {b["key"]: b for b in blockers(campaign)}
    unknown = [k for k in keys if k not in by_key]
    if unknown:
        raise GateError(f"unknown blocker key(s): {', '.join(unknown)}")
    unsupported = [k for k in keys if by_key[k]["kind"] != "human"]
    if unsupported and not waive_unsupported:
        raise GateError("unsupported rules cannot be confirmed; waive them explicitly with a note, "
                        "or park/archive the campaign: " + ", ".join(unsupported))
    if unsupported and not (note or "").strip():
        raise GateError("waiving an unsupported rule requires a note")
    meta = dict(campaign.source_metadata or {})
    conf = dict(meta.get("rules_confirmations") or {})
    now = datetime.now(timezone.utc).isoformat()
    for k in keys:
        conf[k] = {"by": actor, "at": now, "note": (note or "")[:500] or None, "text": by_key[k]["text"][:300],
                   "type": "waiver" if by_key[k]["kind"] != "human" else "confirmation"}
    meta["rules_confirmations"] = conf
    campaign.source_metadata = meta
    still = pending(campaign)
    released = False
    if not still and campaign.status == NEEDS_REVIEW:
        prev = ((meta.get("rules_gate") or {}).get("previous_status")) or "scored"
        if prev not in GATED_FROM:
            prev = "scored"
        released = record_auto_transition(
            campaign, prev, reason=f"rules confirmed by human ({len(keys)} item(s))", actor=actor
        )
    return {"confirmed": keys, "pending": still, "released": released, "status": campaign.status}
