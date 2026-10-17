"""drive_resolver_tick parks hidden-brief campaigns as blocked_no_assets/needs_join."""
from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from app.db.database import SessionLocal
from app.models.campaign import Campaign

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("resolver_tick", ROOT / "scripts" / "drive_resolver_tick.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def camp():
    s = SessionLocal()
    made = []

    def make(meta, status="briefed"):
        c = Campaign(name=f"nj-{uuid.uuid4().hex[:8]}", status=status, source_provider="whop",
                     source_url=f"https://whop.com/experiences/exp_T/campaigns/{uuid.uuid4().hex}",
                     source_metadata=meta)
        s.add(c)
        s.commit()
        s.refresh(c)
        made.append(c.id)
        return c

    yield s, make
    s.rollback()
    s.execute(text("DELETE FROM campaigns WHERE id = ANY(:ids)"), {"ids": made})
    s.commit()
    s.close()


def _run(m, monkeypatch, *argv):
    called = []
    import app.services.asset_resolve as ar
    monkeypatch.setattr(ar, "expand_all", lambda urls: called.append(urls) or ([], ["no_ingestible_urls"]))
    monkeypatch.setattr(sys, "argv", ["drive_resolver_tick.py", *argv])
    assert m.main() == 0
    return called


def test_hidden_brief_goes_to_blocked_no_assets(camp, monkeypatch):
    s, make = camp
    c = make({"detail": {"brief_counts": {"references": 2}}})
    called = _run(_load(), monkeypatch, "--campaign-id", str(c.id))
    assert called == []  # never sent to the resolver
    s.expire_all()
    row = s.get(Campaign, c.id)
    assert row.status == "blocked_no_assets"
    assert row.source_metadata["resolve_error"]["kind"] == "needs_join"
    assert row.source_metadata["resolve_error"]["hidden_references"] == 2
    h = row.source_metadata["status_history"][-1]
    assert h["from"] == "briefed" and h["to"] == "blocked_no_assets" and "needs_join" in h["reason"]


def test_hidden_brief_dry_run_writes_nothing(camp, monkeypatch):
    s, make = camp
    c = make({"detail": {"brief_counts": {"references": 1}}})
    _run(_load(), monkeypatch, "--campaign-id", str(c.id), "--dry-run")
    s.expire_all()
    assert s.get(Campaign, c.id).status == "briefed"


def test_no_brief_counts_keeps_old_failed_resolve(camp, monkeypatch):
    s, make = camp
    c = make({"detail": {}})
    called = _run(_load(), monkeypatch, "--campaign-id", str(c.id))
    assert called == [[]]
    s.expire_all()
    row = s.get(Campaign, c.id)
    assert row.status == "failed_resolve"
    assert row.source_metadata["resolve_error"]["kind"] == "unsupported_source"


def test_report_script_is_read_only(camp):
    s, make = camp
    c = make({"detail": {"brief_counts": {"references": 1}}}, status="failed_resolve")
    spec = importlib.util.spec_from_file_location("report_hidden", ROOT / "scripts" / "report_hidden_briefs.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    found = m.rows(s, ["failed_resolve"])
    assert (c.id, "failed_resolve", 1, c.name) in found
    s.expire_all()
    assert s.get(Campaign, c.id).status == "failed_resolve"
