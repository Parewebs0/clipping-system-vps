"""Decider score thresholds: >= 0.80 approve, < 0.60 reject, middle stays pending."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.no_db

from app.services.candidate_thresholds import (
    APPROVE,
    KEEP,
    REJECT,
    apply_thresholds,
    default_approve_min,
    default_reject_below,
    threshold_action,
    validate_thresholds,
)

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "score,expected",
    [
        (0.95, APPROVE),
        (0.80, APPROVE),
        (0.7999, KEEP),
        (0.70, KEEP),
        (0.60, KEEP),
        (0.5999, REJECT),
        (0.10, REJECT),
        (None, KEEP),
        ("x", KEEP),
    ],
)
def test_threshold_action_pending(score, expected):
    assert threshold_action("pending", score, 0.80, 0.60) == expected


@pytest.mark.parametrize("status", ["needs_review", "approved", "rejected", "rendered", "superseded"])
def test_threshold_action_only_touches_pending(status):
    assert threshold_action(status, 0.99, 0.80, 0.60) == KEEP
    assert threshold_action(status, 0.10, 0.80, 0.60) == KEEP


def test_defaults_and_env(monkeypatch):
    monkeypatch.delenv("DECIDER_APPROVE_MIN", raising=False)
    monkeypatch.delenv("DECIDER_REJECT_BELOW", raising=False)
    assert default_approve_min() == 0.80
    assert default_reject_below() == 0.60
    monkeypatch.setenv("DECIDER_APPROVE_MIN", "0.9")
    monkeypatch.setenv("DECIDER_REJECT_BELOW", "bad")
    assert default_approve_min() == 0.9
    assert default_reject_below() == 0.60


def test_validate_thresholds():
    validate_thresholds(0.8, 0.6)
    with pytest.raises(ValueError):
        validate_thresholds(0.5, 0.6)
    with pytest.raises(ValueError):
        validate_thresholds(1.5, 0.6)


def _cands():
    return [
        SimpleNamespace(id="a", status="pending", score=0.85),
        SimpleNamespace(id="b", status="pending", score=0.70),
        SimpleNamespace(id="c", status="pending", score=0.40),
        SimpleNamespace(id="d", status="needs_review", score=0.95),
        SimpleNamespace(id="e", status="needs_review", score=0.20),
    ]


def test_apply_thresholds_calls_lifecycle():
    approved, rejected = [], []
    counts = apply_thresholds(
        "db", _cands(), approve_min=0.80, reject_below=0.60,
        approve=lambda db, cid: approved.append(cid) or {"status": "approved"},
        reject=lambda db, cid, reason=None: rejected.append((cid, reason)),
        log=lambda _m: None,
    )
    assert approved == ["a"]
    assert rejected == [("c", "auto: score 0.40 < 0.60")]
    assert counts == {APPROVE: 1, REJECT: 1, KEEP: 3}


def test_apply_thresholds_dry_run_writes_nothing():
    calls = []
    counts = apply_thresholds(
        "db", _cands(), approve_min=0.80, reject_below=0.60, dry_run=True,
        approve=lambda *a, **k: calls.append(a),
        reject=lambda *a, **k: calls.append(a),
        log=lambda _m: None,
    )
    assert calls == []
    assert counts[APPROVE] == 1 and counts[REJECT] == 1


def _load(name):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_one_off_script_is_dry_run_by_default(monkeypatch):
    mod = _load("apply_candidate_thresholds")
    seen = {}

    def fake_apply(db, rows, **kw):
        seen.update(kw)
        return {APPROVE: 0, REJECT: 0, KEEP: 0}

    class _Q:
        def filter(self, *a):
            return self

        def order_by(self, *a):
            return self

        def all(self):
            return []

    class _DB:
        def query(self, *a):
            return _Q()

        def rollback(self):
            pass

        def close(self):
            pass

    import app.db.database as database
    import app.services.candidate_thresholds as th

    monkeypatch.setattr(th, "apply_thresholds", fake_apply)
    monkeypatch.setattr(database, "SessionLocal", lambda: _DB())
    monkeypatch.setattr("sys.argv", ["apply_candidate_thresholds.py"])
    assert mod.main() == 0
    assert seen["dry_run"] is True
    monkeypatch.setattr("sys.argv", ["apply_candidate_thresholds.py", "--apply"])
    assert mod.main() == 0
    assert seen["dry_run"] is False


def test_decider_tick_has_threshold_flags():
    src = (ROOT / "scripts" / "grok_clip_decider_tick.py").read_text()
    assert "--approve-min" in src and "--reject-below" in src
    assert "apply_thresholds(" in src
