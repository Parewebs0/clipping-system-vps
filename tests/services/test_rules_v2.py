"""#33 — RuleSet v2: sources, structured mapping, sanitize, enforcement, coverage."""
import json
from pathlib import Path
from types import SimpleNamespace

from app.services.rules.enforcement import assign_enforcement, blocking_items, legacy_rules, pending_blockers
from app.services.rules.extract import extract_ruleset, llm_to_ruleset, normative_lines, quote_in, norm, sanitize
from app.services.rules.schema import Evidence, RuleSet, UnsupportedRule, load_ruleset
from app.services.rules.sources import SourceBundle, gather_sources, html_to_text
from app.services.rules.structured import apply_structured

FX = Path(__file__).resolve().parents[1] / "fixtures" / "rules"


def _campaign(i):
    fx = json.loads((FX / f"campaign_{i}.json").read_text())
    return SimpleNamespace(id=i, name=fx["name"], source_url=fx["source_url"], source_provider="whop",
                           source_metadata={"discovered": fx["discovered"], "detail": fx["detail"]}), fx


def _fetch_fixture(i):
    def fetch(url):
        return html_to_text((FX / f"doc_{i}.html").read_text()), None
    return fetch


def test_html_export_keeps_hidden_links():
    text = html_to_text((FX / "doc_18.html").read_text())
    assert "GOOGLE DRIVE (https://drive.google.com/drive/folders/14hsa8iKW0kFY9yAZBTsTUvxr_lgoGelk" in text
    raw = '<p><a href="https://www.google.com/url?q=https://x.io/a&amp;sa=D">Assets</a></p>'
    assert html_to_text(raw) == "Assets (https://x.io/a)"


def test_gather_sources_reads_doc_untruncated():
    c, _ = _campaign(13)
    b = gather_sources(c, fetch_doc=_fetch_fixture(13))
    assert len(b.docs) == 1 and b.docs[0]["ok"]
    assert "Use this exact caption, word for word" in b.full_text()
    assert "Pre-order now." in b.full_text()


def test_structured_mapping_only_truthy():
    rs = apply_structured(RuleSet(), {"brandLogo": True, "linkInBio": False, "faceOnCamera": False, "preApproval": True,
                                      "videoLength": 10}, {"sound": {"keepOriginalAudio": False}})
    assert rs.logo.required and rs.pre_approval.required
    assert rs.duration.min_s == 10
    assert rs.account_requirements == [] and rs.unsupported == []
    assert not rs.audio.original_only
    rs = apply_structured(RuleSet(), {"linkInBio": True, "linkInBioUrl": "youtube.com/@jackneel"}, {})
    assert rs.account_requirements[0].kind == "link_in_bio"


def test_sanitize_drops_ungrounded_unsupported_and_fixes_lists():
    rs = RuleSet()
    rs.unsupported = [UnsupportedRule(text="face on camera", evidence=[]),
                      UnsupportedRule(text="x", evidence=[Evidence(quote="q", verified=True)])]
    rs.copy_rules.ftc.tokens = ["#Ad", "#Sponsored"]
    rs.copy_rules.hashtags.ordered = ["#Ad"]
    rs.prohibitions.terms = ["Kanye", "Exaggerated or over-the-top hooks"]
    rs.on_screen_text.must_include = ["BOXABL", "Why didn't anyone tell me you can make music"]
    rs = sanitize(rs)
    assert [u.text for u in rs.unsupported] == ["x"]
    assert rs.copy_rules.hashtags.ordered == []
    assert rs.prohibitions.terms == ["Kanye"] and "Exaggerated or over-the-top hooks" in rs.prohibitions.topics
    assert rs.on_screen_text.must_include == ["BOXABL"]


def test_enforcement_rules():
    rs = RuleSet()
    rs.logo.required = True
    rs.source.only_provided = True
    rs.source.allowed_urls = ["https://f.io/Uqz4Au1N"]
    rs.audio.must_use_provided_sound = True
    rs.hook.required = True
    rs = assign_enforcement(rs)
    assert (rs.duration.min_s, rs.duration.max_s) == (15.0, 45.0)
    assert rs.logo.enforcement == "human" and rs.logo.scope == "campaign"
    assert rs.source.enforcement == "unsupported"
    assert rs.audio.enforcement == "unsupported"
    assert rs.hook.enforcement == "human" and rs.hook.scope == "clip"
    rs2 = RuleSet()
    rs2.logo.required = True
    rs2.logo.url = "https://drive.google.com/file/d/14mlwFoPPLWTftO5BnLVrP4NtIfUFHFmQ/view"
    assert assign_enforcement(rs2).logo.enforcement == "auto"


def test_blockers_and_confirmations():
    rs = RuleSet()
    rs = apply_structured(rs, {"preApproval": True, "linkInBio": True, "linkInBioUrl": "x.com/y"}, {})
    rs = assign_enforcement(rs)
    items = blocking_items(rs)
    kinds = sorted(i["rule"] for i in items if i["kind"] == "human")
    assert kinds == ["account:link_in_bio", "pre_approval"]
    conf = {items[0]["key"]: {"by": "jesus"}}
    assert len(pending_blockers(rs, conf)) == len(items) - 1
    # keys are stable across re-reads
    assert [i["key"] for i in blocking_items(assign_enforcement(apply_structured(RuleSet(), {"preApproval": True, "linkInBio": True, "linkInBioUrl": "x.com/y"}, {})))] == [i["key"] for i in items]


def test_quote_matching_and_normative_lines():
    text = norm("Tag @boxabl on TikTok, Instagram Reels, and YouTube Shorts.\nPayout: $2")
    assert quote_in("Tag @boxabl on TikTok, Instagram Reels", text)
    assert not quote_in("No clips of Kanye", text)
    lines = normative_lines("HASHTAGS:\nMust use all three in this order: #a #b\nhttps://x.io\nPayout $1k")
    assert lines == ["Must use all three in this order: #a #b"]


def test_extract_with_fake_llm_runs_coverage_pass():
    c, _ = _campaign(18)
    b = gather_sources(c, fetch_doc=_fetch_fixture(18))
    calls = []

    def llm(prompt, stage=None, campaign_id=None):
        calls.append(stage)
        if stage == "rules_reader":
            return {"captions": {"required": True, "brand_dictionary": ["BOXABL"],
                                 "evidence": ["Auto-captions must spell \"BOXABL\" correctly or the clip is rejected."]},
                    "copy": {"mentions_by_platform": {"youtube": ["@boxabl"]},
                             "evidence": ["Tag @boxabl on TikTok, Instagram Reels, and YouTube Shorts."]},
                    "unsupported": [{"text": "face on camera", "reason": "", "evidence": []}]}
        assert stage == "rules_coverage"
        return {"items": [{"i": 0, "action": "field", "field": "prohibitions", "add": ["Kanye"]},
                          {"i": 1, "action": "manual"}, {"i": 2, "action": "ignore"}]}

    rs = assign_enforcement(extract_ruleset(b, llm, campaign_id=18))
    assert calls == ["rules_reader", "rules_coverage"]
    assert rs.captions.required and rs.captions.evidence[0].verified and rs.captions.evidence[0].source == "guidelines"
    assert rs.unsupported == []  # ungrounded dropped
    assert rs.logo.required and rs.pre_approval.required  # structured
    cov = rs.coverage
    assert cov.normative_lines > 5 and cov.covered_first_pass >= 2
    assert cov.mapped_second_pass == 2 and cov.ignored == 1
    assert len(cov.uncovered) == cov.normative_lines - cov.covered_first_pass - 3
    assert rs.meta["docs"][0]["ok"] and "text" not in rs.meta["docs"][0]
    # roundtrip through source_metadata
    assert load_ruleset({"ruleset": rs.dump()}).captions.brand_dictionary == ["BOXABL"]


def test_legacy_view():
    rs = RuleSet()
    rs.copy_rules.mentions_by_platform = {"youtube": ["@SEGA_West"], "tiktok": ["@sth_game"]}
    rs.copy_rules.hashtags.ordered = ["#forgeguipartner", "#robloxdev"]
    rs.copy_rules.exact_caption = "Exact."
    rs = assign_enforcement(rs)
    lg = legacy_rules(rs)
    assert lg["tags_required"] == ["@SEGA_West", "@sth_game"] and lg["hashtags"] == ["#forgeguipartner", "#robloxdev"]
    assert lg["caption_must_include"] == "Exact." and lg["duration_min"] == 15.0


def test_llm_to_ruleset_tolerates_bad_sections():
    rs, errors = llm_to_ruleset({"duration": {"min_s": "abc"}, "copy": {"mentions_by_platform": {"YouTube": ["@a"]}}},
                                SourceBundle(guidelines="x"))
    assert errors and errors[0].startswith("duration")
    assert rs.copy_rules.mentions_by_platform == {"youtube": ["@a"]}
