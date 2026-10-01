"""Minimal xAI Chat Completions client. Every call is logged to llm_usage."""
from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx

from app.services.llm_usage import record_usage


def grok_chat_json(
    prompt: str,
    *,
    timeout: float = 60.0,
    stage: str | None = None,
    campaign_id: int | None = None,
    asset_id: Any = None,
) -> dict[str, Any]:
    key = os.environ.get("XAI_API_KEY") or ""
    if not key:
        raise RuntimeError("XAI_API_KEY missing")
    base = os.environ.get("XAI_API_BASE", "https://api.x.ai/v1").rstrip("/")
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
    data: dict[str, Any] = {}
    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(
                f"{base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        record_usage(
            model=model, usage=None, latency_ms=int((time.monotonic() - t0) * 1000),
            stage=stage, campaign_id=campaign_id, asset_id=asset_id,
            ok=False, error=f"{type(e).__name__}: {e}",
        )
        raise
    record_usage(
        model=data.get("model") or model,
        usage=data.get("usage"),
        latency_ms=int((time.monotonic() - t0) * 1000),
        stage=stage, campaign_id=campaign_id, asset_id=asset_id,
        extra={"requested_model": model, "prompt_chars": len(prompt)},
    )
    content = data["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    return json.loads(content)
