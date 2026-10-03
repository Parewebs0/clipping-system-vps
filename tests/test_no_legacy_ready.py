"""Guard: cron scripts only reference campaign statuses that exist (#18).

The legacy 'ready' status was dropped in migration 0012. The scripts are
parsed with ``ast`` (not imported) because importing them configures file
logging as a side effect.
"""
from __future__ import annotations

import ast
from pathlib import Path

from app.models.campaign import CampaignStatus

ROOT = Path(__file__).resolve().parents[1]
VALID = {s.value for s in CampaignStatus}


def _tuple_constant(script: str, name: str) -> tuple[str, ...]:
    tree = ast.parse((ROOT / "scripts" / script).read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return tuple(ast.literal_eval(node.value))
    raise AssertionError(f"{name} not found in {script}")


def test_download_enqueue_only_workable():
    from app.services.campaign_transitions import WORKABLE_STATUSES

    assert WORKABLE_STATUSES == ("scored",)
    src = (ROOT / "scripts" / "download_enqueue_tick.py").read_text()
    assert "Campaign.status.in_(WORKABLE_STATUSES)" in src


def test_discovery_active_statuses_are_valid():
    statuses = _tuple_constant("whop_discovery.py", "ACTIVE_STATUSES")
    assert "ready" not in statuses
    assert set(statuses) <= VALID
