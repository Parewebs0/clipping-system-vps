"""#45 — publication copy from the RuleSet; must pass the #43 copy checks."""
import json
from pathlib import Path
from types import SimpleNamespace

from app.services.publish_copy import CAPTION_MAX, copy_platforms, paid_promotion, platform_copy, platform_mentions
from app.services.rules.render_spec import direct_image_url
from app.services.rules.schema import RuleSet, load_ruleset
from app.services.rules.verifier import check_copy

FX = Path(__file__).resolve().parents[1] / "fixtures" / "rules"


def _camp(rs_dict, name="[whop] · $1 · $2 · Campaign"):
    return SimpleNamespace(id=1, name=name, source_provider="whop", source_metadata={"ruleset": rs_dict})


def _cand(caption="I used this and it was cheap and amazing. Watch it.", title="Great clip"):
    return SimpleNamespace(extra_metadata={"title": title, "caption": caption}, reasoning="")


def _fails(rs, title, desc):
    return [c for c in check_copy(rs, "youtube", title, desc) if c["status"] == "fail"]


def test_copy_6_forgegui_hashtags_ftc_first_after_text():
    rsd = json.loads((FX / "ruleset_6.json").read_text())
    rs = load_ruleset({"ruleset": rsd})
    title, desc, tags = platform_copy(_camp(rsd), None, _cand(caption="I used ForgeGUI to build this UI."), "youtube")
    lines = desc.splitlines()
    assert lines[-1].startswith("#forgeguipartner #robloxdev #roblox")
    assert _fails(rs, title, desc) == []


def test_copy_18_boxabl_mentions():
    rsd = json.loads((FX / "ruleset_18.json").read_text())
    rs = load_ruleset({"ruleset": rsd})
    title, desc, _ = platform_copy(_camp(rsd), None, _cand(caption="This tiny home unfolds in minutes."), "youtube")
    assert "@boxabl" in desc and "Boxabl" in desc
    assert _fails(rs, title, desc) == []
    assert platform_mentions(_camp(rsd), "youtube") == ["@boxabl"]


def test_copy_13_exact_caption_and_own_line_ad():
    rsd = json.loads((FX / "ruleset_13.json").read_text())
    rs = load_ruleset({"ruleset": rsd})
    title, desc, _ = platform_copy(_camp(rsd), None, _cand(), "youtube")
    assert rs.copy_rules.exact_caption and desc.startswith(rs.copy_rules.exact_caption.strip())
    assert any(ln.strip() == "#Ad" for ln in desc.splitlines())
    assert _fails(rs, title, desc) == []


def test_prohibited_sentence_removed():
    rs = RuleSet()
    rs.prohibitions.terms = ["cheap"]
    title, desc, _ = platform_copy(_camp(rs.dump()), None, _cand(), "youtube")
    assert "cheap" not in desc.lower() and "Watch it." in desc
    assert _fails(rs, title, desc) == []


def test_without_ruleset_falls_back_to_legacy_and_paid_flag():
    c = SimpleNamespace(id=1, name="x", source_provider="whop", source_metadata={})
    title, desc, tags = platform_copy(c, None, _cand(), "youtube")
    assert "#Shorts" in tags
    assert paid_promotion(c) is True
    assert paid_promotion(SimpleNamespace(source_provider="manual")) is False


def test_copy_platforms_ignores_x_and_defaults_youtube():
    assert copy_platforms(None) == ["youtube"]
    assert copy_platforms([]) == ["youtube"]
    assert copy_platforms(["x"]) == []
    assert copy_platforms(["YouTube", "X", "tiktok", "instagram"]) == ["youtube", "tiktok", "instagram"]


def test_18_mentions_on_tiktok_and_instagram_without_shorts():
    rsd = json.loads((FX / "ruleset_18.json").read_text())
    rs = load_ruleset({"ruleset": rsd})
    camp, cand = _camp(rsd), _cand(caption="This tiny home unfolds in minutes.")
    for platform in ("youtube", "tiktok", "instagram"):
        title, desc, tags = platform_copy(camp, None, cand, platform)
        assert len(desc) <= CAPTION_MAX[platform]
        assert "@boxabl" in desc and "Boxabl" in desc
        assert [c for c in check_copy(rs, platform, title, desc) if c["status"] == "fail"] == []
        if platform == "youtube":
            assert any(t.lower() == "#shorts" for t in tags)
        else:
            assert all(t.lower() != "#shorts" for t in tags)
            assert "#shorts" not in desc.lower()


def test_13_platform_mentions_and_caption_cap():
    rsd = json.loads((FX / "ruleset_13.json").read_text())
    rs = load_ruleset({"ruleset": rsd})
    camp = _camp(rsd)
    expect = {"youtube": "@SEGA_West", "tiktok": "@sth_game", "instagram": "@sth_game"}
    for platform, mention in expect.items():
        title, desc, tags = platform_copy(camp, None, _cand(), platform)
        assert mention in desc
        assert len(desc) <= CAPTION_MAX[platform]
        assert any(ln.strip() == "#Ad" for ln in desc.splitlines())
        assert [c for c in check_copy(rs, platform, title, desc) if c["status"] == "fail"] == []
        if platform != "youtube":
            assert all(t.lower() != "#shorts" for t in tags)


def test_long_body_keeps_mentions_and_ftc_under_tiktok_cap():
    rs = RuleSet()
    rs.copy_rules.ftc.required = True
    rs.copy_rules.ftc.tokens = ["#Ad"]
    rs.copy_rules.ftc.own_line = True
    rs.copy_rules.mentions_by_platform = {"tiktok": ["@boxabl"]}
    title, desc, _ = platform_copy(_camp(rs.dump()), None, _cand(caption="Word " * 2000), "tiktok")
    assert len(desc) <= 2200
    assert "@boxabl" in desc and "#Ad" in desc.splitlines()
    assert "#shorts" not in desc.lower()
    assert [c for c in check_copy(rs, "tiktok", title, desc) if c["status"] == "fail"] == []


def test_drive_logo_url_direct():
    u = "https://drive.google.com/file/d/14mlwFoPPLWTftO5BnLVrP4NtIfUFHFmQ/view?usp=sharing"
    assert direct_image_url(u) == "https://drive.google.com/uc?export=download&id=14mlwFoPPLWTftO5BnLVrP4NtIfUFHFmQ"
    assert direct_image_url("https://cdn.x/logo.png") == "https://cdn.x/logo.png"
