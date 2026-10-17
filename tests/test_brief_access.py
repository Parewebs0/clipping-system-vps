"""Hidden Whop brief (briefCounts.references > 0, no reference URLs) and 403 parking."""
from __future__ import annotations

import urllib.error

import pytest

from app.services.brief_access import NEEDS_JOIN, hidden_reference_count
from app.services.campaign_closed import FORBIDDEN_REASON, disposition
from app.services.discovery.whop_catalog import detail_summary


def _meta(refs=1, **extra):
    meta = {"detail": {"brief_counts": {"references": refs}, "reference_material": []},
            "asset_links": [], "reference_materials": [], "discovered": {"reference_materials": []},
            "rules": {"content_source_urls": []}}
    meta.update(extra)
    return meta


def test_hidden_when_counts_positive_and_no_urls():
    assert hidden_reference_count(_meta(refs=2)) == 2
    assert NEEDS_JOIN == "needs_join"


@pytest.mark.parametrize("meta", [
    _meta(refs=0),
    {"detail": {}},
    {},
    None,
    _meta(refs="x"),
    _meta(asset_links=["https://drive.google.com/drive/folders/abc"]),
    _meta(rules={"content_source_urls": ["https://kick.com/gattouz0"]}),
    _meta(discovered={"reference_materials": [{"url": "https://docs.google.com/document/d/x"}]}),
])
def test_not_hidden(meta):
    assert hidden_reference_count(meta) == 0


def test_detail_summary_keeps_brief_counts():
    s = detail_summary({"status": "active", "briefCounts": {"contentRules": 1, "references": "3"},
                        "configuration": {}})
    assert s["brief_counts"] == {"contentRules": 1, "creatorRequirements": 0, "references": 3}
    assert detail_summary({"configuration": {}})["brief_counts"] == {}


def test_forbidden_reason_parks_not_archives():
    assert disposition([FORBIDDEN_REASON], {}) == "parked"
