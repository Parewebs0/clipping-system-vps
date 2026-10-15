"""Retries and secret redaction for Grok and public video URLs. No database."""
from __future__ import annotations

import json
import urllib.error

import httpx
import pytest
import requests

from app.api.jobs import _head_video_url
from app.services import asset_resolve, grok_client
from app.services.discovery.brief_extractor import fetch_url_to_bytes
from app.services.http_retry import redact

pytestmark = pytest.mark.no_db


def test_redact_strips_bearer_and_keys():
    raw = "Authorization: Bearer xai-secretvalue token=abc sk-livekey"
    cleaned = redact(raw)
    assert "xai-secretvalue" not in cleaned
    assert "abc" not in cleaned
    assert "sk-livekey" not in cleaned
    assert "[redacted]" in cleaned


class _Resp:
    def __init__(self, code: int, body: dict | None = None):
        self.status_code = code
        self._body = body or {}
        self.request = httpx.Request("POST", "https://api.x.ai/v1/chat/completions")

    def raise_for_status(self):
        if self.status_code >= 400:
            response = httpx.Response(self.status_code, request=self.request)
            raise httpx.HTTPStatusError("status", request=self.request, response=response)

    def json(self):
        return self._body


class _Client:
    script: list = []
    calls = 0

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, *args, **kwargs):
        _Client.calls += 1
        action = _Client.script.pop(0)
        if isinstance(action, Exception):
            raise action
        return action


def _ok_body():
    return {
        "model": "grok-test",
        "choices": [{"message": {"content": json.dumps({"clips": []})}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def test_grok_retries_timeout_then_succeeds(monkeypatch):
    recorded = []
    _Client.script = [httpx.TimeoutException("timed out"), _Resp(200, _ok_body())]
    _Client.calls = 0
    monkeypatch.setenv("XAI_API_KEY", "xai-secretvalue")
    monkeypatch.setattr(grok_client.httpx, "Client", _Client)
    monkeypatch.setattr(grok_client, "record_usage", lambda **kw: recorded.append(kw))
    slept = []
    out = grok_client.grok_chat_json("hi", sleep=slept.append)
    assert out == {"clips": []}
    assert _Client.calls == 2
    assert slept == [0.4]
    assert recorded[-1].get("ok", True) is True
    assert "xai-secretvalue" not in str(recorded)


def test_grok_does_not_retry_client_error(monkeypatch):
    recorded = []
    _Client.script = [_Resp(400, {})]
    _Client.calls = 0
    monkeypatch.setenv("XAI_API_KEY", "xai-secretvalue")
    monkeypatch.setattr(grok_client.httpx, "Client", _Client)
    monkeypatch.setattr(grok_client, "record_usage", lambda **kw: recorded.append(kw))
    with pytest.raises(RuntimeError, match="HTTP 400"):
        grok_client.grok_chat_json("hi", sleep=lambda _s: None)
    assert _Client.calls == 1
    assert recorded[-1]["ok"] is False
    assert "xai-secretvalue" not in recorded[-1]["error"]


def test_grok_missing_key_is_not_retried(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="XAI_API_KEY missing"):
        grok_client.grok_chat_json("hi")


def test_head_check_retries_timeout_then_ok(monkeypatch):
    class _Body:
        status = 200
        headers = {"Content-Type": "video/mp4", "Content-Length": "12"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    calls = {"n": 0}

    def _open(req, timeout=0):
        calls["n"] += 1
        if calls["n"] == 1:
            raise urllib.error.URLError("timed out")
        return _Body()

    monkeypatch.setattr(asset_resolve.urllib.request, "urlopen", _open)
    ok, size, ctype = asset_resolve.head_check("https://example.test/a.mp4", sleep=lambda _s: None)
    assert calls["n"] == 2
    assert ok is True
    assert size == 12
    assert ctype == "video/mp4"


def test_head_check_does_not_retry_404(monkeypatch):
    calls = {"n": 0}

    def _open(req, timeout=0):
        calls["n"] += 1
        raise urllib.error.HTTPError("https://example.test/a.mp4", 404, "missing", {}, None)

    monkeypatch.setattr(asset_resolve.urllib.request, "urlopen", _open)
    assert asset_resolve.head_check("https://example.test/a.mp4", sleep=lambda _s: None) == (False, None, "")
    assert calls["n"] == 1


def test_fetch_url_retries_connection_error(monkeypatch):
    calls = {"n": 0}

    class _R:
        status_code = 200
        content = b"ok"

        def raise_for_status(self):
            return None

    def _get(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.ConnectionError("reset")
        return _R()

    monkeypatch.setattr("app.services.discovery.brief_extractor.requests.get", _get)
    assert fetch_url_to_bytes("https://example.test/brief", sleep=lambda _s: None) == b"ok"
    assert calls["n"] == 2


def test_transcribe_head_retries_then_accepts(monkeypatch):
    calls = {"n": 0}

    class _R:
        def __init__(self, code):
            self.status_code = code

    def _head(url, timeout=0, follow_redirects=False):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("refused")
        return _R(200)

    monkeypatch.setattr("app.api.jobs.httpx.head", _head)
    assert _head_video_url("https://www.youtube.com/watch?v=abc", sleep=lambda _s: None) is None
    assert calls["n"] == 2


def test_transcribe_head_404_is_final(monkeypatch):
    calls = {"n": 0}

    class _R:
        status_code = 404

    def _head(url, timeout=0, follow_redirects=False):
        calls["n"] += 1
        return _R()

    monkeypatch.setattr("app.api.jobs.httpx.head", _head)
    err = _head_video_url("https://www.tiktok.com/@u/video/1", sleep=lambda _s: None)
    assert err is not None
    assert "HTTP 404" in err
    assert calls["n"] == 1
