"""Resolver reads URLs written in the brief text (source_instructions / Whop description)."""
from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app.db.database import SessionLocal
from app.models.asset import Asset
from app.models.campaign import Campaign
from app.services.asset_resolve import expand_all, urls_from_text

ROOT = Path(__file__).resolve().parents[1]

SPEED = (
    "Clip/Create content from the Expedia Unpack Segment on IShowSpeed's stream 10.14.26\n\n"
    "VOD Link: https://drive.google.com/file/d/1pcUMz8ZpbIcku3ylk6dhOi6xjWAT3juc/view?usp=sharing\n\n"
    "Submissions MUST FOLLOW the rules and requirements.\n"
    "https://reachclipping.com/Expedia-Speed"
)
CEJUDO = (
    "Clip/Create content from the Double Coverage x Henry Cejudo Podcast\n\n"
    "Read the rules and requirements here:\nhttps://reachclipping.com/HenryCejudo"
)


def _load():
    spec = importlib.util.spec_from_file_location("resolver_tick_text", ROOT / "scripts" / "drive_resolver_tick.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_urls_from_text_dedupes_trims_and_skips_watermark_lines():
    body = (
        SPEED
        + "\nAgain: https://drive.google.com/file/d/1pcUMz8ZpbIcku3ylk6dhOi6xjWAT3juc/view?usp=sharing."
        + "\nWatermark: https://drive.google.com/file/d/WATERMARKxxxxxxxxxxxx/view"
        + "\nUse the logo (https://drive.google.com/file/d/LOGOxxxxxxxxxxxxxxxxx/view)"
    )
    assert urls_from_text(body) == [
        "https://drive.google.com/file/d/1pcUMz8ZpbIcku3ylk6dhOi6xjWAT3juc/view?usp=sharing",
        "https://reachclipping.com/Expedia-Speed",
    ]
    assert urls_from_text("") == []
    assert urls_from_text(None) == []


def test_text_urls_read_discovered_description_and_skip_known():
    tick = _load()
    c = SimpleNamespace(
        source_instructions="ruleset v2: 0 unsupported, 0 account reqs, 0 manual checks",
        source_metadata={"discovered": {"description": SPEED}},
    )
    known = ["https://reachclipping.com/Expedia-Speed"]
    assert tick._text_urls(c, known) == [
        "https://drive.google.com/file/d/1pcUMz8ZpbIcku3ylk6dhOi6xjWAT3juc/view?usp=sharing",
    ]


def test_drive_vod_in_text_becomes_downloadable_asset_and_sheet_is_ignored():
    urls = urls_from_text(SPEED + "\nTracker: https://docs.google.com/spreadsheets/d/abcSHEET/edit")
    found, errors = expand_all(urls)
    assert [(a["source_provider"], a["source_id"]) for a in found] == [
        ("gdrive", "1pcUMz8ZpbIcku3ylk6dhOi6xjWAT3juc"),
    ]
    assert found[0]["source_url"] == (
        "https://drive.google.com/uc?export=download&id=1pcUMz8ZpbIcku3ylk6dhOi6xjWAT3juc"
    )
    assert errors == ["unsupported_source:https://reachclipping.com/Expedia-Speed"]


def test_sheet_only_text_is_not_a_video():
    found, errors = expand_all(urls_from_text("Clip Context Tracker https://docs.google.com/spreadsheets/d/x/edit"))
    assert found == []
    assert errors == ["no_ingestible_urls"]


@pytest.fixture
def camp():
    s = SessionLocal()
    made = []

    def make(meta, *, instructions=None, status="briefed"):
        c = Campaign(name=f"tx-{uuid.uuid4().hex[:8]}", status=status, source_provider="whop",
                     source_url=f"https://whop.com/experiences/exp_T/campaigns/{uuid.uuid4().hex}",
                     source_instructions=instructions, source_metadata=meta)
        s.add(c)
        s.commit()
        s.refresh(c)
        made.append(c.id)
        return c

    yield s, make
    s.rollback()
    s.execute(text("DELETE FROM assets WHERE campaign_id = ANY(:ids)"), {"ids": made})
    s.execute(text("DELETE FROM campaigns WHERE id = ANY(:ids)"), {"ids": made})
    s.commit()
    s.close()


def _run(monkeypatch, cid):
    monkeypatch.setattr(sys, "argv", ["drive_resolver_tick.py", "--campaign-id", str(cid)])
    assert _load().main() == 0


def test_brief_with_drive_vod_resolves(camp, monkeypatch):
    s, make = camp
    c = make({"discovered": {"description": SPEED}}, instructions="ruleset v2: notes")
    _run(monkeypatch, c.id)
    s.expire_all()
    row = s.get(Campaign, c.id)
    assert row.status == "assets_resolved"
    assets = s.query(Asset).filter(Asset.campaign_id == c.id).all()
    assert [(a.source_provider, a.source_id) for a in assets] == [("gdrive", "1pcUMz8ZpbIcku3ylk6dhOi6xjWAT3juc")]


def test_landing_only_fails_with_the_url_on_record(camp, monkeypatch):
    s, make = camp
    c = make({"discovered": {"description": CEJUDO}}, instructions=CEJUDO)
    _run(monkeypatch, c.id)
    s.expire_all()
    row = s.get(Campaign, c.id)
    assert row.status == "failed_resolve"
    err = row.source_metadata["resolve_error"]
    assert err["kind"] == "unsupported_source"
    assert "reachclipping.com/HenryCejudo" in err["message"]


def test_hidden_brief_with_only_a_landing_still_needs_join(camp, monkeypatch):
    s, make = camp
    c = make({"detail": {"brief_counts": {"references": 3}}, "discovered": {"description": CEJUDO}})
    _run(monkeypatch, c.id)
    s.expire_all()
    row = s.get(Campaign, c.id)
    assert row.status == "blocked_no_assets"
    assert row.source_metadata["resolve_error"]["kind"] == "needs_join"
