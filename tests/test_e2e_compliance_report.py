"""2d: scripts/e2e_compliance_report.py — Markdown report of the rules verifier.

run() resolves the clip (by uuid or latest), builds the Markdown from a
verifier- shaped report and never mutates the clip unless store=True is passed
with rerun=True (same write POST /api/clips/{id}/verify does).
main() is tested live against the test DB: exit code, header line and --out.
"""
from __future__ import annotations

import argparse
import importlib.util
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from app.db.database import SessionLocal

ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("e2e_compliance_report", ROOT / "scripts" / "e2e_compliance_report.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture(scope="module")
def script():
    return _load()


def _args(clip_id=None, latest=False, campaign_id=None, rerun=False, store=False, out=None):
    return argparse.Namespace(clip_id=clip_id, latest=latest, campaign_id=campaign_id,
                              rerun=rerun, store=store, out=out)


# --- render_report: table and counts from a verifier-shaped report ----------------

def _example_report():
    return {
        "status": "fail",
        "version": 1,
        "ruleset_version": 2,
        "checked_at": "2026-10-10T12:00:00+00:00",
        "failed": ["duration", "copy.ftc"],
        "review": ["hook", "edit"],
        "checks": [
            {"rule": "duration", "status": "fail", "expected": [15, 45], "actual": 60.2,
             "how": "ffprobe del render (worker)", "detail": ""},
            {"rule": "aspect", "status": "pass", "expected": "1080x1920", "actual": "1080x1920",
             "how": "ffprobe del render (worker)"},
            {"rule": "copy.ftc", "status": "fail", "expected": {"tokens": ["#Ad"]}, "actual": {"line": None},
             "how": "copy de publicación (youtube): líneas del texto", "detail": "falta el marcador",
             "platform": "youtube"},
            {"rule": "copy.platform.x", "status": "n/a", "expected": None, "actual": None,
             "how": "X queda fuera de alcance (#54)"},
            {"rule": "hook", "status": "review", "expected": 3, "actual": None,
             "how": "humano al aprobar el publish"},
            {"rule": "edit", "status": "review", "expected": [], "actual": None,
             "how": "humano al aprobar el publish"},
        ],
    }


def test_render_report_header_counts_and_table(script):
    md = script.render_report(_example_report())
    assert md.startswith("# Informe de cumplimiento del clip")
    assert "**Resultado global:** ❌ no cumple" in md
    assert "Verificador:** v1 · ruleset v2" in md and "2026-10-10T12:00" in md
    # 1 pass + 1 n/a + 2 fail + 2 review
    assert "1 pasa / 2 falla / 2 revisión humana / 1 n/a" in md
    for fragment in ("| Regla | Resultado | Esperado | Real | Cómo se verificó |",
                     "`duration`", "`aspect`", "`copy.ftc (youtube)`", "`copy.platform.x`",
                     "ffprobe del render (worker)", "falta el marcador",
                     "**Pendiente de confirmación humana al aprobar el publish:** `hook`, `edit`"):
        assert fragment in md, fragment


def test_render_report_status_badges_and_pipe_escaping(script):
    report = {
        "status": "pass", "version": 1, "ruleset_version": 2, "checked_at": "t",
        "failed": [], "review": [],
        "checks": [
            {"rule": "prohibitions.terms", "status": "pass", "expected": ["a|b"], "actual": [],
             "how": "copy + subtítulos: búsqueda litera|l"},
        ],
    }
    md = script.render_report(report)
    assert "**Resultado global:** ✅ cumple" in md
    assert "0 falla" in md and "0 revisión humana" in md
    # the cell renderer escapes literal pipes: row = 6 fields -> 6 unescaped pipes
    row = [ln for ln in md.splitlines() if ln.startswith("| `prohibitions.terms`")][0]
    assert row.count("\\|") == 2 and row.count("|") == 8
    assert "a\\|b" in row and "litera\\|l" in row


def test_render_report_empty_checks(script):
    md = script.render_report({"status": "pending", "checks": []})
    assert "Sin checks" in md and "**Resultado global:** ⏳ pendiente" in md


# --- run(): clip resolution, meta and read-only guarantee -------------------------

@pytest.fixture
def db():
    s = SessionLocal()
    made: list = []
    yield s, made
    s.rollback()
    if made:
        s.execute(text("DELETE FROM clips WHERE id = ANY(:ids)"), {"ids": [c.id for c in made]})
        s.commit()
    s.close()


RS = {
    "version": 2, "platforms": ["youtube"],
    "duration": {"required": True, "min_s": 10, "max_s": 60},
    "aspect": {"required": True, "ratio": "9:16"},
    "captions": {"required": True},
    "logo": {"required": False},
    "audio": {"required": False},
    "hook": {"required": False},
    "edit": {"required": False},
    "copy_rules": {"required": False},
    "language": {"required": False},
    "prohibitions": {"required": False},
    "manual_checks": [],
}


_STORED = {"status": "pass", "version": 1, "ruleset_version": 2,
           "checked_at": "2026-10-10T14:00:00+00:00", "failed": [], "review": [],
           "checks": [{"rule": "aspect", "status": "pass", "expected": "1080x1920",
                       "actual": "1080x1920", "how": "ffprobe"}]}


def _mkclip(db, made, *, ruleset=RS):
    from app.models.asset import Asset
    from app.models.campaign import Campaign
    from app.models.clip import Clip
    from app.models.job import Job

    # Candidate registers the candidates table (FK target of clips.candidate_id)
    from app.models import candidate as _cand_mod
    assert _cand_mod.Candidate is not None

    c = Campaign(name=f"2d-{uuid.uuid4().hex[:8]}", source_provider="whop", status="scored",
                 source_metadata={"ruleset": ruleset})
    db.add(c); db.commit()
    a = Asset(campaign_id=c.id, source_url="https://e/v", source_provider="youtube", source_id="x",
              asset_type="video", status="transcribed", extra_metadata={"transcription": {"language": "en"}})
    db.add(a); db.commit()
    rj = Job(job_type="render", status="completed",
             payload={"asset_id": str(a.id), "render_spec": {"required": {}}},
             result={"render_spec_version": 2,
                     "probe": {"width": 1080, "height": 1920, "fps": 30.0, "has_audio": True,
                               "duration": 30.0, "codec": "h264"},
                     "applied": {"captions": {"applied": True, "events": 4, "text": "hello world"},
                                 "watermark": {"applied": False},
                                 "on_screen_text": {"applied": False, "texts": []}}})
    db.add(rj); db.commit()
    clip = Clip(campaign_id=c.id, asset_id=a.id, render_job_id=rj.id, file_path=f"{uuid.uuid4().hex}.mp4",
                duration_seconds=30.0, qa_status="pending", qa_result={}, status="created",
                compliance_status="pending", compliance_report={})
    db.add(clip); db.commit()
    made.append(clip)
    return clip


def test_run_by_clip_id_with_stored_report_is_read_only(script, db):
    s, made = db
    clip = _mkclip(s, made)
    stored = {
        "status": "pass", "version": 1, "ruleset_version": 2, "checked_at": "2026-10-10T12:00:00+00:00",
        "failed": [], "review": ["hook"], "checks": [
            {"rule": "aspect", "status": "pass", "expected": "1080x1920", "actual": "1080x1920",
             "how": "ffprobe del render (worker)"},
            {"rule": "hook", "status": "review", "expected": 3, "actual": None, "how": "humano"}],
    }
    clip.compliance_report = stored
    s.commit()

    code, md, meta = script.run(_args(clip_id=clip.id), s)
    assert code == 0
    assert md.startswith("# Informe de cumplimiento del clip")
    assert "`aspect`" in md and "1 pasa" in md
    assert meta["clip_id"] == str(clip.id) and meta["campaign_id"] == clip.campaign_id
    assert meta["status"] == "pass" and meta["review"] == ["hook"]
    # read-only: the stored report is untouched and compliance_status unchanged
    s.expire_all()
    row = s.execute(text("SELECT compliance_status, compliance_report FROM clips WHERE id=:i"),
                    {"i": clip.id}).one()
    assert row.compliance_status == "pending"
    assert row.compliance_report["checked_at"] == "2026-10-10T12:00:00+00:00"


def test_run_by_clip_id_without_report_suggests_rerun(script, db):
    s, made = db
    clip = _mkclip(s, made)
    code, md, meta = script.run(_args(clip_id=clip.id), s)
    assert code == 1 and md == "" and meta["error"].startswith("empty compliance report")
    assert clip.compliance_status == "pending"


def test_run_missing_clip_and_campaign(script, db):
    s, _ = db
    code, md, meta = script.run(_args(clip_id=uuid.uuid4()), s)
    assert code == 1 and "not found" in meta["error"]

    code, _, meta = script.run(_args(latest=True, campaign_id=999999), s)
    assert code == 1 and "campaign 999999 not found" in meta["error"]


def test_run_stored_report_no_rerun_by_default(script, db, monkeypatch):
    s, made = db
    clip = _mkclip(s, made)
    clip.compliance_report = {"status": "fail", "checks": [{"rule": "duration", "status": "fail"}],
                              "failed": ["duration"], "review": []}
    s.commit()

    def boom(*a, **k):  # rerun must not be silently invoked by default
        raise AssertionError("verify_clip called without --rerun")

    import app.services.rules.verifier as verifier
    monkeypatch.setattr(verifier, "verify_clip", boom)
    code, md, meta = script.run(_args(clip_id=clip.id), s)
    assert code == 0 and meta["status"] == "fail" and "duration" in md


def test_run_rerun_verifies_without_storing(script, db, monkeypatch):
    s, made = db
    clip = _mkclip(s, made)
    real = {"called": 0}

    def fake_verify(db_, clip_, store_missing=False):
        real["called"] += 1
        return {"status": "pass", "version": 1, "ruleset_version": 2,
                "checked_at": "2026-10-10T13:00:00+00:00", "failed": [], "review": [],
                "checks": [{"rule": "aspect", "status": "pass", "expected": "1080x1920",
                            "actual": "1080x1920", "how": "ffprobe"}]}

    import app.services.rules.verifier as verifier
    monkeypatch.setattr(verifier, "verify_clip", fake_verify)
    code, md, meta = script.run(_args(clip_id=clip.id, rerun=True), s)
    assert code == 0 and real["called"] == 1
    assert meta["status"] == "pass" and "2026-10-10T13:00" in md
    s.expire_all()
    row = s.execute(text("SELECT compliance_status FROM clips WHERE id=:i"), {"i": clip.id}).one()
    assert row.compliance_status == "pending"  # not stored


def test_run_latest_picks_most_recently_updated(script, db):
    s, made = db
    old = _mkclip(s, made)
    newer = _mkclip(s, made)
    old.compliance_report = _STORED
    s.commit()
    newer.compliance_report = {**_STORED, "checked_at": "2026-10-10T15:00:00+00:00"}
    s.commit()

    code, md, meta = script.run(_args(latest=True), s)
    assert code == 0 and meta["clip_id"] == str(newer.id)

    # campaign filter picks the campaign's own clip even when it is older
    code, _, meta = script.run(_args(latest=True, campaign_id=old.campaign_id), s)
    assert code == 0 and meta["clip_id"] == str(old.id)
    assert meta["campaign_id"] == old.campaign_id


def test_main_live_against_test_db(script, db, capsys):
    """Integration: main() wires SessionLocal exactly like the other ticks."""
    s, made = db
    clip = _mkclip(s, made)
    clip.compliance_report = dict(_STORED, checked_at="2026-10-10T14:00:00+00:00")
    s.commit()
    out_path = ROOT / f".2d-report-{uuid.uuid4().hex[:8]}.md"
    try:
        code = script.main(["--clip-id", str(clip.id), "--out", str(out_path)])
        out = capsys.readouterr().out
        assert code == 0
        assert out.startswith(f"clip={clip.id} campaign={clip.campaign_id} compliance=pending failed=0")
        assert "# Informe de cumplimiento del clip" in out
        assert out_path.exists() and "Informe de cumplimiento" in out_path.read_text()
        assert not clip.compliance_report or clip.compliance_report.get("checked_at") == "2026-10-10T14:00:00+00:00"  # default run is read-only

        code = script.main(["--clip-id", str(clip.id), "--rerun"])
        out = capsys.readouterr().out
        # read-only rerun: fresh pass report printed, but clip state unchanged
        assert code == 0 and "`aspect`" in out and "`duration`" in out
        s.expire_all()
        s.refresh(clip)
        assert clip.compliance_status == "pending"  # verify without store
        # store=True persists the fresh report (same as POST /clips/{id}/verify)
        code = script.main(["--clip-id", str(clip.id), "--rerun", "--store"])
        out = capsys.readouterr().out
        assert code == 0 and "compliance=pass" in out
        s.expire_all()
        s.refresh(clip)
        assert clip.compliance_status == "pass"
        assert clip.compliance_report["status"] == "pass"
    finally:
        out_path.unlink(missing_ok=True)


def test_argparser_rejects_store_without_rerun(script, capsys):
    rc = script.main(["--latest", "--store"])
    out = capsys.readouterr()
    assert rc == 2 and "--store solo tiene sentido" in out.err