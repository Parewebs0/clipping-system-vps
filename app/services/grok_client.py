"""Minimal xAI Chat Completions client. Every call is logged to llm_usage."""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Callable

import httpx

from app.services.http_retry import RETRY_STATUS, pause, redact
from app.services.llm_usage import record_usage

logger = logging.getLogger(__name__)

_ATTEMPTS = 3
_BACKOFF_S = 0.4


def _usage_extra(model: str, prompt: str, extra: dict[str, Any] | None) -> dict[str, Any]:
    out: dict[str, Any] = {"requested_model": model, "prompt_chars": len(prompt)}
    if extra:
        out.update(extra)
    return out


def _fail(reason: str) -> RuntimeError:
    return RuntimeError(f"grok request failed: {redact(reason)}")


def request_chat_completion(
    payload: dict[str, Any],
    *,
    timeout: float = 60.0,
    attempts: int = _ATTEMPTS,
    sleep: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """POST /chat/completions. Retries timeouts, connection errors, 429 and 5xx.

    Does not record llm_usage. Raises RuntimeError with a redacted message.
    A missing API key is not retried.
    """
    key = os.environ.get("XAI_API_KEY") or ""
    if not key:
        raise RuntimeError("XAI_API_KEY missing")
    base = os.environ.get("XAI_API_BASE", "https://api.x.ai/v1").rstrip("/")
    url = f"{base}/chat/completions"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    last_reason = "no response"
    for attempt in range(1, attempts + 1):
        try:
            with httpx.Client(timeout=timeout) as client:
                response = client.post(url, headers=headers, json=payload)
            code = getattr(response, "status_code", 200)
            if code in RETRY_STATUS and attempt < attempts:
                last_reason = f"HTTP {code}"
                logger.warning("grok %s, retry %s/%s", code, attempt, attempts)
                pause(attempt, backoff_s=_BACKOFF_S, sleep=sleep)
                continue
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise _fail("response was not a JSON object")
            return data
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            last_reason = f"HTTP {code}"
            if code in RETRY_STATUS and attempt < attempts:
                logger.warning("grok %s, retry %s/%s", code, attempt, attempts)
                pause(attempt, backoff_s=_BACKOFF_S, sleep=sleep)
                continue
            raise _fail(last_reason) from exc
        except httpx.TransportError as exc:
            last_reason = type(exc).__name__
            if attempt < attempts:
                logger.warning("grok %s, retry %s/%s", last_reason, attempt, attempts)
                pause(attempt, backoff_s=_BACKOFF_S, sleep=sleep)
                continue
            raise _fail(last_reason) from exc
    raise _fail(last_reason)


def grok_chat_json(
    prompt: str,
    *,
    timeout: float = 60.0,
    stage: str | None = None,
    campaign_id: int | None = None,
    asset_id: Any = None,
    extra: dict[str, Any] | None = None,
    attempts: int = _ATTEMPTS,
    sleep: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    key = os.environ.get("XAI_API_KEY") or ""
    if not key:
        raise RuntimeError("XAI_API_KEY missing")
    model = os.environ.get("XAI_MODEL", "grok-4-1-fast")
    payload = {
        "model": model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": "Return only valid JSON. No markdown.",
            },
            {"role": "user", "content": prompt},
        ],
    }
    t0 = time.monotonic()
    try:
        data = request_chat_completion(
            payload, timeout=timeout, attempts=attempts, sleep=sleep,
        )
    except Exception as exc:
        record_usage(
            model=model, usage=None, latency_ms=int((time.monotonic() - t0) * 1000),
            stage=stage, campaign_id=campaign_id, asset_id=asset_id,
            ok=False, error=redact(f"{type(exc).__name__}: {exc}"),
            extra=_usage_extra(model, prompt, extra),
        )
        raise
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        record_usage(
            model=data.get("model") or model,
            usage=data.get("usage"),
            latency_ms=int((time.monotonic() - t0) * 1000),
            stage=stage, campaign_id=campaign_id, asset_id=asset_id,
            ok=False, error="grok response missing message content",
            extra=_usage_extra(model, prompt, extra),
        )
        raise RuntimeError("grok response missing message content") from exc
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        record_usage(
            model=data.get("model") or model,
            usage=data.get("usage"),
            latency_ms=int((time.monotonic() - t0) * 1000),
            stage=stage, campaign_id=campaign_id, asset_id=asset_id,
            ok=False, error="grok response was not valid JSON",
            extra=_usage_extra(model, prompt, extra),
        )
        raise RuntimeError("grok response was not valid JSON") from exc
    if not isinstance(parsed, dict):
        record_usage(
            model=data.get("model") or model,
            usage=data.get("usage"),
            latency_ms=int((time.monotonic() - t0) * 1000),
            stage=stage, campaign_id=campaign_id, asset_id=asset_id,
            ok=False, error="grok response JSON was not an object",
            extra=_usage_extra(model, prompt, extra),
        )
        raise RuntimeError("grok response JSON was not an object")
    record_usage(
        model=data.get("model") or model,
        usage=data.get("usage"),
        latency_ms=int((time.monotonic() - t0) * 1000),
        stage=stage, campaign_id=campaign_id, asset_id=asset_id,
        extra=_usage_extra(model, prompt, extra),
    )
    return parsed
