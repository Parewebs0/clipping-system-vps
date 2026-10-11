"""Guarantee the shadow package never writes and never imports approve_candidate."""
from __future__ import annotations

from pathlib import Path

from app.shadow.decider_graph.graph import run_decider
from app.shadow.decider_graph.nodes import ReadOnlySession, wrap_db
from tests.shadow.helpers import ASSET_ID, fake_llm, packed_asset, runtime_for

SHADOW_ROOT = Path(__file__).resolve().parents[2] / "app" / "shadow"
CLIP = {
    "start": 0.0,
    "end": 22.0,
    "title": "ForgeGUI shop UI in seconds",
    "caption": "ForgeGUI builds a Roblox shop UI.",
    "on_screen_text": "ForgeGUI",
    "hook_end": 2.0,
    "reason": "demo",
}


def test_source_has_no_writes_or_approve():
    hits = []
    needles = ("approve_candidate", ".commit(", ".flush(")
    for path in SHADOW_ROOT.rglob("*"):
        if path.suffix != ".py":
            continue
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            if needle in text:
                hits.append(f"{path.relative_to(SHADOW_ROOT.parent.parent)}: {needle}")
    assert hits == []


def test_not_in_production_install():
    root = SHADOW_ROOT.parents[1]
    req = (root / "requirements.txt").read_text(encoding="utf-8")
    docker = (root / "Dockerfile").read_text(encoding="utf-8")
    assert "langgraph" not in req.lower()
    assert "requirements-shadow" not in docker


def test_readonly_session_blocks_writes():
    class Inner:
        def add(self, obj):
            raise AssertionError("inner add")

        def commit(self):
            raise AssertionError("inner commit")

        def flush(self):
            raise AssertionError("inner flush")

        def get(self, *a, **k):
            return None

    db = wrap_db(Inner())
    assert isinstance(db, ReadOnlySession)
    for method in ("add", "commit", "flush", "delete"):
        try:
            getattr(db, method)(None)
            raise AssertionError(f"{method} should have raised")
        except RuntimeError as e:
            assert "read-only" in str(e)


def test_run_decider_does_not_touch_session_writes():
    packed = packed_asset()

    class Bomb:
        def add(self, *a, **k):
            raise AssertionError("add")

        def commit(self):
            raise AssertionError("commit")

        def flush(self):
            raise AssertionError("flush")

        def get(self, *a, **k):
            raise AssertionError("graph should use snapshots, not db.get")

        def query(self, *a, **k):
            raise AssertionError("query")

    rt = runtime_for(
        packed,
        llm=fake_llm([CLIP]),
        db=Bomb(),
        approval_threshold=10_000,
        similar_examples=[],
    )
    result = run_decider(ASSET_ID, rt)
    assert result["report"]["decision"] in {"clips", "none"}
    assert "approve_candidate" not in str(result)
