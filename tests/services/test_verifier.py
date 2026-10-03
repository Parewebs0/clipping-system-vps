"""#43 — post-render rules verifier."""
import uuid

from app.services.rules.enforcement import assign_enforcement
from app.services.rules.schema import RuleSet
from app.services.rules.verifier import check_copy, verify


def _rs():
    rs = RuleSet()
    rs.duration.min_s, rs.duration.max_s = 15, 45
    rs.captions.required = True
    rs.captions.brand_dictionary = ["BOXABL"]
    rs.logo.required = True
    rs.logo.url = "https://x/logo.png"
    rs.on_screen_text.required = True
    rs.on_screen_text.must_include = ["@boxabl"]
    rs.language.code = "en"
    cp = rs.copy_rules
    cp.mentions_by_platform = {"youtube": ["@boxabl"]}
    cp.must_mention_any = ["Boxabl", "Casita"]
    cp.hashtags.ordered = ["#boxabl", "#casita"]
    cp.hashtags.position = "after_text"
    cp.ftc.required = True
    cp.ftc.tokens = ["#Ad"]
    cp.ftc.own_line = True
    cp.ftc.first_after_text = True
    rs.prohibitions.terms = ["cheap"]
    rs.manual_checks = []
    return assign_enforcement(rs)


GOOD_RESULT = {
    "render_spec_version": 2,
    "probe": {"width": 1080, "height": 1920, "fps": 30.0, "has_audio": True, "duration": 30.0, "codec": "h264"},
    "applied": {
        "captions": {"applied": True, "events": 9, "text": "the BOXABL casita folds out"},
        "on_screen_text": {"applied": True, "texts": [{"text": "Tag @boxabl", "start": 0, "end": 3}]},
        "watermark": {"applied": True, "position": "top_right", "start": None, "end": None},
    },
}
GOOD_COPY = ("Boxabl Casita in 1 hour", "Watch the Boxabl Casita unfold @boxabl\n#Ad\n#boxabl #casita")


def _verify(rs, result=GOOD_RESULT, copy=GOOD_COPY, lang="en"):
    return verify(rs, result=result, required={"width": 1080, "height": 1920}, duration_window=(15, 45),
                  clip_duration=30.0, tx_language=lang, copies={"youtube": copy})


def test_all_pass():
    r = _verify(_rs())
    assert r["status"] == "pass", [c for c in r["checks"] if c["status"] == "fail"]
    rules = {c["rule"] for c in r["checks"]}
    assert {"duration", "aspect", "audio", "captions", "captions.brand_dictionary", "logo", "on_screen_text",
            "language", "copy.mentions.youtube", "copy.must_mention_any", "copy.hashtags", "copy.ftc",
            "prohibitions.terms"} <= rules
    assert all(c["how"] for c in r["checks"])


def test_render_failures():
    bad = {**GOOD_RESULT, "probe": {**GOOD_RESULT["probe"], "width": 720, "has_audio": False, "duration": 60.0},
           "applied": {"captions": {"applied": True, "events": 3, "text": "the Boxabl casita"},
                       "on_screen_text": {"applied": False, "texts": []},
                       "watermark": {"applied": False}}}
    r = _verify(_rs(), result=bad, lang="es")
    assert set(r["failed"]) >= {"duration", "aspect", "audio", "captions.brand_dictionary", "logo",
                                "on_screen_text", "language"}
    assert r["status"] == "fail"


def test_old_worker_without_metadata_fails():
    r = _verify(_rs(), result={"file_path": "x"})
    assert "render.metadata" in r["failed"]


def test_copy_failures():
    rs = _rs()
    bad = check_copy(rs, "youtube", "Cheap house", "#casita #boxabl Nice house #Ad")
    failed = {c["rule"] for c in bad if c["status"] == "fail"}
    assert failed == {"copy.mentions.youtube", "copy.hashtags", "copy.ftc", "prohibitions.terms"}
    # hashtags before text
    c = [x for x in check_copy(rs, "youtube", "", "#boxabl #casita\nBoxabl @boxabl\n#Ad") if x["rule"] == "copy.hashtags"][0]
    assert c["status"] == "fail" and "antes" in c["detail"]
    # FTC first line → fails first_after_text
    c = [x for x in check_copy(rs, "youtube", "", "#Ad\nBoxabl @boxabl #boxabl #casita") if x["rule"] == "copy.ftc"][0]
    assert c["status"] == "fail"


def test_exact_caption():
    rs = RuleSet()
    rs.copy_rules.exact_caption = "Download Forge GUI today"
    ok = check_copy(rs, "youtube", "t", "Download  forge gui today\n#roblox")
    assert ok[0]["status"] == "pass"
    ko = check_copy(rs, "youtube", "t", "Get Forge GUI")
    assert ko[0]["status"] == "fail"


def test_x_is_not_applicable_and_does_not_fail():
    rs = _rs()
    rs.platforms = ["youtube", "x", "tiktok"]
    r = _verify(rs)
    xs = [c for c in r["checks"] if c["rule"] == "copy.platform.x"]
    assert len(xs) == 1 and xs[0]["status"] == "n/a"
    assert r["status"] == "pass"
    assert "copy.platform.x" not in r["failed"]


def test_hook_end_passes_fails_or_stays_review():
    rs = _rs()
    rs.hook.required = True
    rs.hook.max_seconds = 2.0
    base = dict(result=GOOD_RESULT, required={"width": 1080, "height": 1920}, duration_window=(15, 45),
                clip_duration=30.0, tx_language="en", copies={"youtube": GOOD_COPY})
    absent = verify(rs, hook_end=None, **base)
    assert "hook" in absent["review"] and "hook" not in absent["failed"]
    ok = verify(rs, hook_end=1.9, **base)
    assert "hook" not in ok["review"] and "hook" not in ok["failed"]
    late = verify(rs, hook_end=2.2, **base)
    assert "hook" in late["failed"]
    # edit is never auto-passed
    rs.edit.required = True
    still = verify(rs, hook_end=1.0, **base)
    assert "edit" in still["review"]


def test_frame_checks_override_metadata_when_present():
    rs = _rs()
    bad = {**GOOD_RESULT, "applied": {**GOOD_RESULT["applied"], "frame_checks": {
        "logo_visible": False, "captions_visible": True, "samples": [{"what": "logo", "t": 1.0}],
    }}}
    r = _verify(rs, result=bad)
    assert "logo" in r["failed"] and "captions" not in r["failed"]
    hidden = {**GOOD_RESULT, "applied": {**GOOD_RESULT["applied"], "frame_checks": {"captions_visible": False, "samples": []}}}
    r = _verify(rs, result=hidden)
    assert "captions" in r["failed"] and "logo" not in r["failed"]


def test_as_cta_logo_must_cover_the_last_three_seconds():
    rs = _rs()
    rs.logo.as_cta = True
    short = {**GOOD_RESULT, "applied": {**GOOD_RESULT["applied"], "watermark": {
        "applied": True, "position": "top_right", "start": 0.0, "end": 3.0,
    }}}
    r = _verify(rs, result=short)
    assert "logo" in r["failed"]
    full = {**GOOD_RESULT, "applied": {**GOOD_RESULT["applied"], "watermark": {
        "applied": True, "position": "top_right", "start": None, "end": None,
    }}}
    r = _verify(rs, result=full)
    assert "logo" not in r["failed"]


def test_human_checks_listed_as_review_not_blocking():
    from app.services.rules.schema import ManualCheck

    rs = _rs()
    rs.manual_checks = [ManualCheck(required=True, text="Prohibido: No clips of Kanye")]
    rs.hook.required = True
    r = _verify(rs)
    assert r["status"] == "pass" and set(r["review"]) == {"manual_check", "hook"}


def test_on_qa_completed_stores_compliance(db):
    """Integration: QA completion runs the verifier and stores the report on the clip."""
    from app.models.asset import Asset
    from app.models.campaign import Campaign
    from app.models.clip import Clip
    from app.models.job import Job
    from app.services.job_state_transitions import on_qa_completed

    c = Campaign(name=f"ver-{uuid.uuid4().hex[:6]}", source_provider="whop", status="scored",
                 source_metadata={"ruleset": _rs().dump()})
    db.add(c)
    db.commit()
    a = Asset(campaign_id=c.id, source_url="https://e/v", source_provider="youtube", source_id="x",
              asset_type="video", status="transcribed", extra_metadata={"transcription": {"language": "en"}})
    db.add(a)
    db.commit()
    rj = Job(job_type="render", status="completed", payload={"asset_id": str(a.id), "render_spec": {"required": {}}},
             result=GOOD_RESULT)
    db.add(rj)
    db.commit()
    clip = Clip(campaign_id=c.id, asset_id=a.id, render_job_id=rj.id, file_path="x.mp4", duration_seconds=30.0,
                qa_status="pending", qa_result={}, status="created")
    db.add(clip)
    db.commit()
    qj = Job(job_type="qa", status="completed", payload={"clip_id": str(clip.id)})
    db.add(qj)
    db.commit()
    on_qa_completed(db, qj, {"status": "pass"})
    db.refresh(clip)
    # #45: the copy is built from the RuleSet → the copy rules pass too
    assert clip.compliance_status == "pass", clip.compliance_report["failed"]
    assert any(c["rule"] == "copy.ftc" and c["status"] == "pass" for c in clip.compliance_report["checks"])
    # a render without the logo must fail
    rj.result = {**GOOD_RESULT, "applied": {**GOOD_RESULT["applied"], "watermark": {"applied": False}}}
    db.commit()
    from app.services.rules.verifier import verify_clip

    verify_clip(db, clip)
    assert clip.compliance_status == "fail" and clip.compliance_report["failed"] == ["logo"]
