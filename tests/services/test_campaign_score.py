"""Issue #25 — scorer uses YouTube rate + remaining budget, no copy/asset-count penalties."""
from app.services.campaign_score import (
    campaign_rate_usd,
    campaign_remaining_usd,
    difficulty_penalties,
    score_campaign,
)


def test_rate_prefers_economics_platform_rate():
    meta = {
        "economics": {"platform_rates": {"youtube": {"rate_usd": 1.5}, "tiktok": {"rate_usd": 3}}},
        "discovered": {"cpm_usd_per_1k": 0.2},
    }
    assert campaign_rate_usd(meta) == 1.5
    assert campaign_rate_usd(meta, "tiktok") == 3


def test_rate_falls_back_to_discovered_payouts_and_never_min_cpm():
    meta = {"discovered": {"cpm_usd_per_1k": 0.3, "payouts": [
        {"platform": "tiktok", "rate_cents": 300},
        {"platform": "youtube", "rate_cents": 120},
    ]}}
    assert campaign_rate_usd(meta) == 1.2
    assert campaign_rate_usd({"discovered": {"cpm_usd_per_1k": 2}}) == 0.0


def test_remaining_from_economics_or_metrics():
    assert campaign_remaining_usd({"economics": {"remaining_usd": 4200}}) == 4200
    meta = {"discovered": {"prize_pool_usd": 10000, "raw": {"metrics": {"budgetSpentCents": 250000}}}}
    assert campaign_remaining_usd(meta) == 7500
    assert campaign_remaining_usd({"discovered": {"prize_pool_usd": 900}}) == 900
    assert campaign_remaining_usd({}) == 0


def test_no_penalty_for_asset_count_or_publication_copy():
    rules = {"tagging_required": True, "caption_required": True, "ftc_disclosure": True,
             "hashtags_required": ["#x"]}
    assert difficulty_penalties(rules, real_assets=12) == {}


def test_production_penalties_kept():
    pens = difficulty_penalties({"watermark_required": True, "on_screen_text_required": True})
    assert pens == {"watermark": 15.0, "on_screen_text": 8.0}


def test_good_campaign_is_eligible():
    s = score_campaign(real_assets=5, cpm=2.0, prize=6000, verified=True)
    assert s["value"] == 100.0 and s["eligible"] and s["block_reason"] is None


def test_higher_rate_and_remaining_score_higher():
    lo = score_campaign(real_assets=3, cpm=1.0, prize=2000, verified=False)["value"]
    hi = score_campaign(real_assets=3, cpm=2.0, prize=5000, verified=False)["value"]
    assert hi > lo


def test_block_reasons():
    assert score_campaign(real_assets=0, cpm=3, prize=9000, verified=True)["block_reason"] == "no_real_assets"
    assert score_campaign(real_assets=4, cpm=0.3, prize=9000, verified=True)["block_reason"] == "rate_below_0.5usd"
    assert score_campaign(real_assets=4, cpm=2, prize=9000, verified=True,
                          content_kinds=["mediasilo"])["block_reason"] == "unsupported_host"
    low = score_campaign(real_assets=1, cpm=0.6, prize=300, verified=False)
    assert low["block_reason"] == "score_below_50" and not low["eligible"]


def test_min_rate_env(monkeypatch):
    monkeypatch.setenv("SCORE_MIN_RATE_USD", "1")
    assert score_campaign(real_assets=4, cpm=0.8, prize=9000, verified=True)["block_reason"] == "rate_below_1usd"
