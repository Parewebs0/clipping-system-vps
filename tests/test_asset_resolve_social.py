"""Pure unit tests for app.services.asset_resolve social-only handling (no DB)."""
from app.services.asset_resolve import classify, expand_all

CAMPAIGN_1_LINKS = [
    "https://www.instagram.com/reels/audio/1054449497150560",
    "https://vt.tiktok.com/ZS9kwfoAEX72R-RNzZK/",
    "https://youtube.com/source/E0n2A-DUy3Y/shorts?si=x19rb4dUDzyref8L",
]


def test_social_links_classified_as_social():
    for u in CAMPAIGN_1_LINKS:
        assert classify(u) == "social", u


def test_social_only_campaign_reports_social_only():
    found, errors = expand_all(CAMPAIGN_1_LINKS)
    assert found == []
    assert errors == ["social_only"]


def test_non_social_hosts_not_misclassified():
    assert classify("https://www.dropbox.com/s/abc123/clip.mp4") == "dropbox_file"
    assert classify("https://netflix.com/watch/1") == "unknown"
    assert classify("https://www.tiktok.com/@user/video/123456") == "tiktok"
    assert classify("https://www.youtube.com/shorts/abcdefgh") == "youtube"


def test_social_plus_ingestible_still_resolves():
    found, errors = expand_all(CAMPAIGN_1_LINKS + ["https://example.com/a/clip.mp4"])
    assert [f["source_provider"] for f in found] == ["direct"]
    assert errors == []


def test_no_links_still_no_ingestible_urls():
    assert expand_all([]) == ([], ["no_ingestible_urls"])
