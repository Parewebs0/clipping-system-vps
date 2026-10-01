"""LLM usage logging. DB tests run against clipping_test (see conftest guard)."""
import json

import pytest
from sqlalchemy import text

from app.services import grok_client, llm_usage
from app.services.llm_usage import estimate_cost, parse_usage, record_usage

P = {"input": 1.25, "cached": 0.20, "output": 2.50, "long_mult": 2.0}


def test_parse_usage_openai_shape():
    t = parse_usage({
        "prompt_tokens": 4000, "completion_tokens": 500, "total_tokens": 4700,
        "prompt_tokens_details": {"cached_tokens": 1000},
        "completion_tokens_details": {"reasoning_tokens": 200},
    })
    assert t == {"prompt_tokens": 4000, "cached_tokens": 1000, "completion_tokens": 500,
                 "reasoning_tokens": 200, "total_tokens": 4700}


def test_estimate_cost_with_cache_and_reasoning():
    t = {"prompt_tokens": 4000, "cached_tokens": 1000, "completion_tokens": 500,
         "reasoning_tokens": 200, "total_tokens": 4700}
    # 3000*1.25 + 1000*0.20 + 700*2.50 = 3750+200+1750 = 5700 / 1e6
    assert estimate_cost(t, P) == pytest.approx(0.0057)


def test_estimate_cost_reasoning_inside_completion():
    t = {"prompt_tokens": 1000, "cached_tokens": 0, "completion_tokens": 300,
         "reasoning_tokens": 100, "total_tokens": 1300}
    assert estimate_cost(t, P) == pytest.approx((1000 * 1.25 + 300 * 2.5) / 1e6)


def test_long_context_doubles():
    t = {"prompt_tokens": 200_000, "cached_tokens": 0, "completion_tokens": 0,
         "reasoning_tokens": 0, "total_tokens": 200_000}
    assert estimate_cost(t, P) == pytest.approx(0.5)


def test_prices_env_override(monkeypatch):
    monkeypatch.setenv("XAI_PRICE_INPUT_PER_M", "3")
    assert llm_usage.prices()["input"] == 3.0


@pytest.fixture
def clean_usage(db):
    db.execute(text("DELETE FROM llm_usage"))
    db.commit()
    yield db
    db.execute(text("DELETE FROM llm_usage"))
    db.commit()


def test_record_usage_persists(clean_usage):
    out = record_usage(model="grok-4-1-fast", usage={"prompt_tokens": 10, "completion_tokens": 5,
                       "total_tokens": 15}, latency_ms=123, stage="brief_reader", campaign_id=7,
                       asset_id="abc")
    assert out["total_tokens"] == 15
    row = clean_usage.execute(text(
        "SELECT stage, campaign_id, asset_id, total_tokens, latency_ms, ok FROM llm_usage")).one()
    assert tuple(row) == ("brief_reader", 7, "abc", 15, 123, True)


def test_record_usage_never_raises(monkeypatch):
    import app.db.database as dbmod

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(dbmod, "SessionLocal", boom)
    out = record_usage(model="m", usage=None, latency_ms=1, stage="x")
    assert out is not None and out["cost_usd"] == 0


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


class _Client:
    payload = None

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, *a, **k):
        return _Resp(self.payload)


def test_grok_chat_json_logs_usage(monkeypatch, clean_usage):
    _Client.payload = {
        "model": "grok-4.3",
        "choices": [{"message": {"content": json.dumps({"clips": []})}}],
        "usage": {"prompt_tokens": 1500, "completion_tokens": 250, "total_tokens": 1750},
    }
    monkeypatch.setenv("XAI_API_KEY", "test-not-real")
    monkeypatch.setattr(grok_client.httpx, "Client", _Client)
    assert grok_client.grok_chat_json("hi", stage="clip_decider", campaign_id=3, asset_id="a1") == {"clips": []}
    row = clean_usage.execute(text(
        "SELECT stage, model, prompt_tokens, completion_tokens, cost_usd FROM llm_usage")).one()
    assert row[0] == "clip_decider" and row[1] == "grok-4.3"
    assert float(row[4]) == pytest.approx((1500 * 1.25 + 250 * 2.5) / 1e6)
