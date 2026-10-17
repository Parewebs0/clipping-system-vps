"""reachclipping.com landings: read the «FOOTAGE» line, nothing else (campaign 56)."""
from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

import app.services.asset_resolve as ar
from app.db.database import SessionLocal
from app.models.asset import Asset
from app.models.campaign import Campaign

ROOT = Path(__file__).resolve().parents[1]
YT = "https://www.youtube.com/watch?is=SBibtNA73ajY91pE&v=YcFH2mL-YXU&feature=youtu.be"
CEJUDO_PAGE = """<html><body>
<div class="meta-card"><button data-copy="FOOTAGE: https://www.youtube.com/watch?is=SBibtNA73ajY91pE&amp;v=YcFH2mL-YXU&amp;feature=youtu.be"></button>
<div class="meta-label">Important Info</div>
<div class="meta-value">FOOTAGE: <a href="https://www.youtube.com/watch?is=SBibtNA73ajY91pE&amp;v=YcFH2mL-YXU&amp;feature=youtu.be">https://www.youtube.com/watch?is=SBibtNA73ajY91pE&amp;v=YcFH2mL-YXU&amp;feature=youtu.be</a></div></div>
<li>Videos must be from the episode. Podcast Link: https://www.youtube.com/watch?v=OTHERvideo1</li>
<li>Videos MUST have the Podcast watermark provided. <a href="https://drive.google.com/file/d/1bVsR2jUqmP5i61TuFWo0NzN7_0dSLAou/view">https://drive.google.com/file/d/1bVsR2jUqmP5i61TuFWo0NzN7_0dSLAou/view</a></li>
<a href="https://reachclipping.com/other">other</a>
</body></html>"""
SPEED_PAGE = """<div>FOOTAGE LINK: \n<a href="https://drive.google.com/file/d/1pcUMz8ZpbIcku3ylk6dhOi6xjWAT3juc/view?usp=sharing">x</a>

ALTERNATIVE FOOTAGE:\nhttps://docs.google.com/document/d/1Kt1tB6TtOCrcjvz5hxvbH0N_aYqp_jE12EfXN1TqOAQ/edit?usp=sharing</div>
<li>Find assets in linked google drive: https://drive.google.com/drive/folders/1vpwA5_0wbOPfBIOHfeCFwp87Eq4eMQ7Z</li>"""


class _Resp:
    def __init__(self, body: str):
        self.body = body.encode()

    def read(self, n=-1):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def pages(monkeypatch):
    served = {}
    calls = []

    def fake_urlopen(req, timeout=None):
        url = req.full_url
        calls.append((url, timeout))
        if url not in served:
            raise OSError("offline")
        return _Resp(served[url])

    monkeypatch.setattr(ar.urllib.request, "urlopen", fake_urlopen)
    return served, calls


def test_footage_line_only_and_html_unescaped(pages):
    served, calls = pages
    served["https://reachclipping.com/HenryCejudo"] = CEJUDO_PAGE
    assert ar.reachclipping_footage("https://reachclipping.com/HenryCejudo") == [YT]
    assert calls[0][1] == ar.REACH_TIMEOUT_S


def test_url_on_the_line_after_the_label(pages):
    served, _ = pages
    served["https://reachclipping.com/Expedia-Speed"] = SPEED_PAGE
    assert ar.reachclipping_footage("https://reachclipping.com/Expedia-Speed") == [
        "https://drive.google.com/file/d/1pcUMz8ZpbIcku3ylk6dhOi6xjWAT3juc/view?usp=sharing",
        "https://docs.google.com/document/d/1Kt1tB6TtOCrcjvz5hxvbH0N_aYqp_jE12EfXN1TqOAQ/edit?usp=sharing",
    ]


def test_landing_expands_to_youtube_asset(pages):
    served, _ = pages
    served["https://reachclipping.com/HenryCejudo"] = CEJUDO_PAGE
    found, errors = ar.expand_all(["https://reachclipping.com/HenryCejudo"])
    assert [(a["source_provider"], a["source_id"]) for a in found] == [("youtube", "YcFH2mL-YXU")]
    assert errors == []


def test_landing_and_text_drive_link_dedupe(pages):
    served, _ = pages
    served["https://reachclipping.com/Expedia-Speed"] = SPEED_PAGE
    found, errors = ar.expand_all([
        "https://drive.google.com/file/d/1pcUMz8ZpbIcku3ylk6dhOi6xjWAT3juc/view?usp=sharing",
        "https://reachclipping.com/Expedia-Speed",
    ])
    assert [(a["source_provider"], a["source_id"]) for a in found] == [
        ("gdrive", "1pcUMz8ZpbIcku3ylk6dhOi6xjWAT3juc"),
    ]
    assert errors == []


def test_unreachable_or_no_footage_stays_unsupported(pages):
    served, _ = pages
    served["https://reachclipping.com/Empty"] = "<p>Read the rules</p>"
    for url in ("https://reachclipping.com/Empty", "https://reachclipping.com/Down"):
        found, errors = ar.expand_all([url])
        assert found == []
        assert errors == [f"unsupported_source:{url}"]


@pytest.fixture
def camp():
    s = SessionLocal()
    made = []

    def make(meta, *, instructions=None, status="briefed"):
        c = Campaign(name=f"rc-{uuid.uuid4().hex[:8]}", status=status, source_provider="whop",
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


def test_hidden_brief_resolves_when_the_landing_gives_footage(camp, pages, monkeypatch):
    served, _ = pages
    served["https://reachclipping.com/HenryCejudo"] = CEJUDO_PAGE
    s, make = camp
    desc = "Read the rules and requirements here:\nhttps://reachclipping.com/HenryCejudo"
    c = make({"detail": {"brief_counts": {"references": 4}}, "discovered": {"description": desc}}, instructions=desc)
    spec = importlib.util.spec_from_file_location("resolver_tick_rc", ROOT / "scripts" / "drive_resolver_tick.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    monkeypatch.setattr(sys, "argv", ["drive_resolver_tick.py", "--campaign-id", str(c.id)])
    assert m.main() == 0
    s.expire_all()
    assert s.get(Campaign, c.id).status == "assets_resolved"
    assets = s.query(Asset).filter(Asset.campaign_id == c.id).all()
    assert [(a.source_provider, a.source_id) for a in assets] == [("youtube", "YcFH2mL-YXU")]
