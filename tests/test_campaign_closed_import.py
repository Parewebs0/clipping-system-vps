"""MOLI-68: campaign_closed and its tick must import in a fresh interpreter.

A module-level import of discovery.whop_catalog from campaign_closed cycles
through discovery/__init__ -> upsert -> merge_prize_pool. Pytest's conftest
imports the app first, which can hide that, so this runs a clean process.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_CLEAN_IMPORT = textwrap.dedent(
    """
    import importlib.util
    from datetime import datetime, timezone
    from pathlib import Path

    import app.services.campaign_closed as campaign_closed

    spec = importlib.util.spec_from_file_location(
        "campaign_closed_tick_clean",
        Path("scripts/campaign_closed_tick.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Same imports scripts/campaign_closed_tick.py:run performs before the DB.
    from app.services.campaign_closed import sync_whop_campaign, thresholds
    from app.services.discovery.whop_catalog import CatalogClient

    assert callable(campaign_closed.merge_prize_pool)
    assert callable(sync_whop_campaign)
    assert callable(thresholds)
    assert callable(CatalogClient)
    assert callable(mod.run)

    # Run the lazy imports. A kept campaign does not touch status transitions.
    reasons, _econ, _summ = campaign_closed.park_reasons({})
    assert isinstance(reasons, list)

    class _Row:
        source_metadata = {}
        prize_pool_usd = None
        budget_history = None
        last_synced_at = None
        status = "scored"

    outcome = campaign_closed.apply_refresh(
        _Row(),
        [],
        {"budget_usd": 10, "target_rate_usd": 1, "remaining_usd": 10},
        {},
        now=datetime.now(timezone.utc),
        actor="test",
    )
    assert outcome == "kept"
    """
)


@pytest.mark.no_db
def test_campaign_closed_and_tick_import_in_clean_process():
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [sys.executable, "-c", _CLEAN_IMPORT],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
