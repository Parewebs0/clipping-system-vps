"""LLM usage logging + cost estimate. Best effort: never raises.

Prices are USD per 1M tokens, env-overridable. Defaults = xAI grok-4.3 pricing,
which is what the retired slug grok-4-1-fast is billed at since 2026-05-15
(docs.x.ai: input $1.25, cached input $0.20, output $2.50; prompts >= 200k
tokens billed at 2x for all tokens).
"""
from __future__ import annotations

import os
import sys
from decimal import Decimal
from typing import Any

LONG_CONTEXT_THRESHOLD = 200_000


def _price(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def prices() -> dict[str, float]:
    return {
        "input": _price("XAI_PRICE_INPUT_PER_M", 1.25),
        "cached": _price("XAI_PRICE_CACHED_INPUT_PER_M", 0.20),
        "output": _price("XAI_PRICE_OUTPUT_PER_M", 2.50),
        "long_mult": _price("XAI_PRICE_LONG_CONTEXT_MULT", 2.0),
    }


def _int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def parse_usage(usage: dict | None) -> dict[str, int]:
    """Normalize an OpenAI-compatible `usage` block (xAI chat completions)."""
    u = usage or {}
    prompt = _int(u.get("prompt_tokens"))
    completion = _int(u.get("completion_tokens"))
    pdet = u.get("prompt_tokens_details") or {}
    cdet = u.get("completion_tokens_details") or {}
    cached = _int(pdet.get("cached_tokens")) or _int(u.get("cached_prompt_text_tokens"))
    reasoning = _int(cdet.get("reasoning_tokens")) or _int(u.get("reasoning_tokens"))
    total = _int(u.get("total_tokens")) or (prompt + completion + reasoning)
    return {
        "prompt_tokens": prompt,
        "cached_tokens": min(cached, prompt) if prompt else cached,
        "completion_tokens": completion,
        "reasoning_tokens": reasoning,
        "total_tokens": total,
    }


def estimate_cost(t: dict[str, int], p: dict[str, float] | None = None) -> float:
    p = p or prices()
    prompt, cached = t.get("prompt_tokens", 0), t.get("cached_tokens", 0)
    # Reasoning tokens are billed as output. xAI may report them inside or
    # outside completion_tokens; total - prompt covers both cases.
    completion = t.get("completion_tokens", 0)
    output = max(completion, t.get("total_tokens", 0) - prompt, 0)
    mult = p["long_mult"] if prompt >= LONG_CONTEXT_THRESHOLD else 1.0
    cost = ((prompt - cached) * p["input"] + cached * p["cached"] + output * p["output"]) / 1_000_000
    return round(cost * mult, 6)


def default_stage() -> str:
    try:
        name = os.path.basename(sys.argv[0] or "")
        return (name.rsplit(".", 1)[0] or "unknown")[:64]
    except Exception:
        return "unknown"


def record_usage(
    *,
    model: str | None,
    usage: dict | None,
    latency_ms: int | None,
    stage: str | None = None,
    campaign_id: int | None = None,
    asset_id: Any = None,
    ok: bool = True,
    error: str | None = None,
    provider: str = "xai",
    extra: dict | None = None,
) -> dict | None:
    """Log one line + insert one llm_usage row in its own session. Never raises."""
    try:
        stage = (stage or default_stage())[:64]
        t = parse_usage(usage)
        cost = estimate_cost(t)
        print(
            f"llm_usage stage={stage} model={model} campaign={campaign_id} asset={asset_id} "
            f"ok={ok} prompt={t['prompt_tokens']} cached={t['cached_tokens']} "
            f"completion={t['completion_tokens']} reasoning={t['reasoning_tokens']} "
            f"total={t['total_tokens']} latency_ms={latency_ms} cost_usd={cost:.6f}",
            file=sys.stderr,
            flush=True,
        )
    except Exception as e:  # pragma: no cover
        print(f"llm_usage log_error {type(e).__name__}: {e}", file=sys.stderr)
        return None
    try:
        from app.db.database import SessionLocal
        from app.models.llm_usage import LlmUsage

        db = SessionLocal()
        try:
            db.add(LlmUsage(
                provider=provider,
                model=(model or "")[:128] or None,
                stage=stage,
                campaign_id=campaign_id if isinstance(campaign_id, int) else None,
                asset_id=str(asset_id)[:64] if asset_id is not None else None,
                latency_ms=latency_ms,
                cost_usd=Decimal(str(cost)),
                ok=ok,
                error=(error or None) and str(error)[:1000],
                extra=extra,
                **t,
            ))
            db.commit()
        finally:
            db.close()
    except Exception as e:
        print(f"llm_usage persist_error {type(e).__name__}: {str(e)[:200]}", file=sys.stderr)
    return {**t, "cost_usd": cost, "stage": stage}
