"""API and discovery errors stay clear and do not leak secrets. No database."""
from __future__ import annotations

import logging
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.discovery import get_scoring, list_providers
from app.api.system import APP_VERSION, system_info
from app.main import app
from app.services.discovery.upsert import fetch_details
from app.services.public_error import public_error

pytestmark = pytest.mark.no_db


def test_public_error_redacts_and_names_the_type():
    text = public_error(RuntimeError("Bearer supersecretvalue while calling grok"))
    assert text.startswith("RuntimeError:")
    assert "supersecretvalue" not in text
    assert "[redacted]" in text


def test_system_info_version_matches_the_app():
    body = system_info(_=True)
    assert body["service"] == "clipping-api"
    assert body["version"] == "0.3.0"
    assert body["version"] == APP_VERSION
    assert app.version == APP_VERSION


def test_discovery_providers_lists_registered_providers():
    body = list_providers(_=True)
    assert body["providers"]
    assert body["providers"][0]["name"]
    assert body["providers"][0]["type"]


def test_discovery_scoring_missing_campaign_is_404():
    class _DB:
        def get(self, model, row_id):
            return None

    with pytest.raises(HTTPException) as caught:
        get_scoring(999, db=_DB(), _=True)
    assert caught.value.status_code == 404
    assert caught.value.detail == "Campaign not found"


def test_fetch_detail_error_keeps_the_card_and_hides_the_token(caplog):
    class _Provider:
        name = "whop"

        def fetch_detail(self, item):
            raise RuntimeError("Bearer supersecretvalue")

    card = SimpleNamespace(external_id="camp-1")
    with caplog.at_level(logging.WARNING):
        out = fetch_details(_Provider(), [card], timeout_s=2)
    assert out == [card]
    assert "supersecretvalue" not in caplog.text
    assert "camp-1" in caplog.text


def test_fetch_detail_timeout_keeps_unfetched_cards():
    class _Provider:
        name = "whop"

        def fetch_detail(self, item):
            time.sleep(0.4)
            return None

    card = SimpleNamespace(external_id="camp-slow")
    out = fetch_details(_Provider(), [card], timeout_s=0.05)
    assert out == [card]
