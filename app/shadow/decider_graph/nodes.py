"""Pure-ish graph nodes. Each takes (state, runtime) and returns a partial state update.

The session is wrapped in ReadOnlySession: add/commit/flush raise. LLM calls never
go through grok_chat_json (that module writes llm_usage).
"""
from __future__ import annotations

import json
import os
import time
from types import SimpleNamespace
from typing import Any, Literal

from app.services.decider import (
    MAX_SEGMENT_BLOCK_CHARS,
    chunk_segments,
    decider_options,
    merge_by_iou,
    parse_subscores,
    snap_window,
    take_top_n,
    window_iou,
)
from app.shadow.decider_graph.prompts import SYSTEM, build_propose_prompt
from app.shadow.decider_graph.state import DeciderState, ProposeClipsResponse, ShadowRuntime

MIN_PICK_SCORE = 0.5
_WRITE_METHODS = (
    "add",
    "add_all",
    "delete",
    "merge",
    "commit",
    "flush",
    "bulk_save_objects",
    "bulk_insert_mappings",
    "bulk_update_mappings",
    "rollback",
)


class ReadOnlySession:
    """Forwards reads; any write method raises. Shadow must never persist."""

    def __init__(self, inner: Any):
        self._inner = inner

    def query(self, *args: Any, **kwargs: Any) -> Any:
        return self._inner.query(*args, **kwargs)

    def get(self, *args: Any, **kwargs: Any) -> Any:
        return self._inner.get(*args, **kwargs)

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("shadow decider is read-only (execute blocked)")

    def __getattr__(self, name: str) -> Any:
        if name in _WRITE_METHODS:
            def _blocked(*_a: Any, **_k: Any) -> Any:
                raise RuntimeError(f"shadow decider is read-only ({name} blocked)")

            return _blocked
        return getattr(self._inner, name)


def wrap_db(db: Any) -> Any:
    if db is None or isinstance(db, ReadOnlySession):
        return db
    return ReadOnlySession(db)


def _campaign_ns(campaign: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        source_metadata=campaign.get("source_metadata") or {},
        spec=campaign.get("spec") or {},
        source_provider=campaign.get("source_provider") or "whop",
        source_instructions=campaign.get("source_instructions"),
        name=campaign.get("name") or "",
        id=campaign.get("id"),
    )


def _asset_ns(asset: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        extra_metadata=asset.get("extra_metadata") or {},
        duration_seconds=asset.get("duration_seconds"),
        id=asset.get("id"),
        campaign_id=asset.get("campaign_id"),
        status=asset.get("status"),
    )


def _snapshot_asset(asset: Any) -> dict[str, Any]:
    return {
        "id": str(asset.id),
        "campaign_id": int(asset.campaign_id),
        "duration_seconds": float(asset.duration_seconds or 0),
        "status": asset.status,
        "extra_metadata": dict(asset.extra_metadata or {}),
    }


def _snapshot_campaign(campaign: Any) -> dict[str, Any]:
    return {
        "id": int(campaign.id),
        "name": campaign.name,
        "status": campaign.status,
        "source_provider": campaign.source_provider,
        "source_instructions": campaign.source_instructions,
        "source_metadata": dict(campaign.source_metadata or {}),
        "spec": dict(campaign.spec or {}),
    }


def load_context(state: DeciderState, runtime: ShadowRuntime) -> dict[str, Any]:
    """Load asset, transcription, campaign. Reads only."""
    asset_id = str(state.get("asset_id") or "")
    snaps = runtime.snapshots or {}
    packed = snaps.get(asset_id) or snaps.get("asset")
    if packed:
        asset = packed["asset"] if "asset" in packed else packed
        campaign = packed.get("campaign") or snaps.get("campaign") or {}
        tx = (asset.get("extra_metadata") or {}).get("transcription") or {}
        segs = [s for s in (tx.get("segments") or []) if isinstance(s, dict)]
        duration = float(asset.get("duration_seconds") or 0) or float(
            tx.get("duration") or 0
        )
        return {
            "asset": asset,
            "campaign": campaign,
            "campaign_id": int(campaign.get("id") or asset.get("campaign_id") or 0),
            "segments": segs,
            "transcript_text": str(tx.get("text") or ""),
            "video_duration": duration,
            "shadow": runtime.shadow,
            "context_iteration": int(state.get("context_iteration") or 0),
            "similar_examples": list(state.get("similar_examples") or []),
            "llm_calls": list(state.get("llm_calls") or []),
            "confidence_threshold": float(runtime.confidence_threshold),
            "error": None,
        }

    db = wrap_db(runtime.db)
    if db is None:
        return {"error": "no asset snapshot and no db"}

    from app.models.asset import Asset
    from app.models.campaign import Campaign

    asset = db.get(Asset, asset_id)
    if asset is None:
        # UUID primary key — accept both str and uuid
        import uuid as _uuid

        try:
            asset = db.get(Asset, _uuid.UUID(asset_id))
        except (ValueError, AttributeError, TypeError):
            asset = None
    if asset is None:
        return {"error": f"asset not found: {asset_id}"}
    campaign = db.get(Campaign, asset.campaign_id)
    snap_a = _snapshot_asset(asset)
    snap_c = _snapshot_campaign(campaign) if campaign is not None else {}
    tx = (snap_a.get("extra_metadata") or {}).get("transcription") or {}
    segs = [s for s in (tx.get("segments") or []) if isinstance(s, dict)]
    duration = float(snap_a.get("duration_seconds") or 0) or float(tx.get("duration") or 0)
    return {
        "asset": snap_a,
        "campaign": snap_c,
        "campaign_id": int(snap_a["campaign_id"]),
        "segments": segs,
        "transcript_text": str(tx.get("text") or ""),
        "video_duration": duration,
        "shadow": runtime.shadow,
        "context_iteration": 0,
        "similar_examples": [],
        "llm_calls": [],
        "confidence_threshold": float(runtime.confidence_threshold),
        "error": None,
    }


def extract_rules(state: DeciderState, runtime: ShadowRuntime) -> dict[str, Any]:
    """Reuse rules/runtime. Does not reimplement extraction."""
    if state.get("error"):
        return {}
    from app.services.rules.runtime import decider_brief, ruleset
    from app.services.silent_clip_cutter import campaign_duration_window

    camp = _campaign_ns(state.get("campaign") or {})
    rs = ruleset(camp)
    dmin, dmax = campaign_duration_window(camp)
    brief = decider_brief(camp) or "- (none beyond duration/format)"
    duration = float(state.get("video_duration") or 0) or dmax
    opts = decider_options(camp)
    return {
        "ruleset": rs.dump() if rs is not None else {},
        "duration_min": float(dmin),
        "duration_max": float(dmax),
        "brief": brief,
        "video_duration": duration,
        "decider_options": opts.as_dict(),
    }


def heuristic_propose(
    segments: list[dict[str, Any]],
    dmin: float,
    dmax: float,
    duration: float,
    *,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Deterministic fallback when no LLM is configured (offline / tests / no key)."""
    usable = []
    for s in segments:
        try:
            a, b = float(s.get("start") or 0), float(s.get("end") or 0)
        except (TypeError, ValueError):
            continue
        if b > a:
            usable.append((a, b, str(s.get("text") or "").strip()))
    if not usable:
        end = min(dmax, duration or dmax)
        return [{
            "start": 0.0,
            "end": round(end, 2),
            "title": "Clip",
            "caption": "",
            "on_screen_text": "",
            "hook_end": 1.5 if end >= 1.5 else None,
            "reason": "heuristic: no segments",
        }]
    clips: list[dict[str, Any]] = []
    i = 0
    while i < len(usable) and len(clips) < max(1, int(limit)):
        start = usable[i][0]
        end = start
        texts: list[str] = []
        j = i
        while j < len(usable):
            end = usable[j][1]
            texts.append(usable[j][2])
            if end - start >= dmin:
                break
            j += 1
        if end - start < max(3.0, dmin * 0.5):
            i += 1
            continue
        if end - start > dmax:
            end = start + dmax
        title = (texts[0] if texts else "Clip")[:80]
        clips.append({
            "start": round(start, 2),
            "end": round(end, 2),
            "title": title,
            "caption": " ".join(texts)[:800],
            "on_screen_text": "",
            "hook_end": 1.5 if (end - start) >= 1.5 else None,
            "reason": "heuristic window from transcript",
        })
        i = j + 1 if j > i else i + 1
    return clips


def _call_llm(prompt: str, runtime: ShadowRuntime) -> tuple[dict[str, Any], dict[str, Any]]:
    if runtime.llm is not None:
        result = runtime.llm(prompt, schema=ProposeClipsResponse)
        if isinstance(result, tuple) and len(result) == 2:
            return result[0], result[1]
        return result, {"model": "injected", "cost_usd": 0.0, "latency_ms": 0}

    key = os.environ.get("XAI_API_KEY") or ""
    if not key:
        return {}, {"model": "none", "cost_usd": 0.0, "latency_ms": 0, "skipped": "no_key"}

    import httpx

    from app.services.llm_usage import estimate_cost, parse_usage

    base = os.environ.get("XAI_API_BASE", "https://api.x.ai/v1").rstrip("/")
    model = os.environ.get("XAI_MODEL", "grok-4-1-fast")
    payload = {
        "model": model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ],
    }
    t0 = time.monotonic()
    with httpx.Client(timeout=60.0) as client:
        r = client.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
        )
        r.raise_for_status()
        data = r.json()
    latency_ms = int((time.monotonic() - t0) * 1000)
    usage = parse_usage(data.get("usage"))
    cost = estimate_cost(usage)
    content = data["choices"][0]["message"]["content"]
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in content
        )
    parsed = json.loads(content)
    stats = {
        "model": data.get("model") or model,
        "latency_ms": latency_ms,
        "cost_usd": cost,
        **usage,
    }
    return parsed, stats


def propose_clips(state: DeciderState, runtime: ShadowRuntime) -> dict[str, Any]:
    if state.get("error"):
        return {"proposed_clips": []}
    segs = list(state.get("segments") or [])
    dmin = float(state.get("duration_min") or 15)
    dmax = float(state.get("duration_max") or 45)
    duration = float(state.get("video_duration") or 0) or dmax
    opts = decider_options(_campaign_ns(state.get("campaign") or {}), {"decider": state.get("decider_options") or {}})
    chunks = chunk_segments(segs, opts)
    n_chunks = max(1, len(chunks))
    calls = list(state.get("llm_calls") or [])
    similar = list(state.get("similar_examples") or [])
    collected: list[dict[str, Any]] = []
    use_heuristic = runtime.llm is None and not (os.environ.get("XAI_API_KEY") or "")

    for ch in chunks or [{"index": 0, "block": "", "start": 0.0, "end": duration, "segments": segs}]:
        block = str(ch.get("block") or "")[:MAX_SEGMENT_BLOCK_CHARS]
        prompt = build_propose_prompt(
            dmin=dmin,
            dmax=dmax,
            duration=duration,
            brief=str(state.get("brief") or ""),
            segment_block=block,
            similar_examples=similar,
            n=opts.candidates_per_chunk,
            chunk=int(ch.get("index") or 0) + 1,
            chunks=n_chunks,
            chunk_start=float(ch.get("start") or 0),
            chunk_end=float(ch.get("end") or duration),
        )
        if use_heuristic:
            clips = heuristic_propose(
                list(ch.get("segments") or segs), dmin, dmax, duration, limit=opts.candidates_per_chunk
            )
            calls.append({
                "stage": "propose_clips",
                "model": "heuristic",
                "cost_usd": 0.0,
                "latency_ms": 0,
                "prompt_chars": len(prompt),
                "chunk_index": ch.get("index"),
                "n_chunks": n_chunks,
            })
        else:
            t0 = time.monotonic()
            raw, stats = _call_llm(prompt, runtime)
            stats = dict(stats or {})
            stats.setdefault("latency_ms", int((time.monotonic() - t0) * 1000))
            stats["stage"] = "propose_clips"
            stats["prompt_chars"] = len(prompt)
            stats["chunk_index"] = ch.get("index")
            stats["n_chunks"] = n_chunks
            calls.append(stats)
            try:
                parsed = ProposeClipsResponse.model_validate(raw if isinstance(raw, dict) else {"clips": []})
            except Exception:
                parsed = ProposeClipsResponse(clips=[])
            clips = [c.model_dump() for c in parsed.clips[: opts.candidates_per_chunk]]
        for clip in clips:
            start, end = snap_window(
                float(clip.get("start") or 0),
                float(clip.get("end") or 0),
                segs,
                max_shift=opts.phrase_shift,
            )
            clip["start"] = start
            clip["end"] = end
            clip["subscores"] = {k: v for k, v in parse_subscores(clip).items() if v is not None}
            from app.services.publish_copy import clip_score

            clip["score"] = clip_score(
                end - start, dmin, dmax, clip.get("score"), clip["subscores"] or None
            )
            clip["chunk_index"] = ch.get("index")
            collected.append(clip)

    merged = merge_by_iou(collected, threshold=opts.iou_threshold)
    return {"proposed_clips": take_top_n(merged, opts.top_n), "llm_calls": calls}


def _duration_score(length: float, dmin: float, dmax: float) -> tuple[bool, float]:
    if dmin <= length <= dmax:
        return True, 1.0
    if length <= 0:
        return False, 0.0
    if length < dmin:
        return False, max(0.0, 1.0 - (dmin - length) / max(dmin, 1.0))
    overflow = length - dmax
    return False, max(0.0, 1.0 - overflow / max(dmax, 1.0))


def score_one(
    clip: dict[str, Any],
    *,
    ruleset_raw: dict[str, Any],
    dmin: float,
    dmax: float,
    asset: dict[str, Any],
    similar: list[dict[str, Any]],
) -> dict[str, Any]:
    from app.services.rules.runtime import segment_text
    from app.services.rules.schema import RuleSet
    from app.services.rules.verifier import check_copy, hook_end_in_clip

    start = float(clip.get("start") or 0)
    end = float(clip.get("end") or 0)
    length = end - start
    duration_ok, dur_s = _duration_score(length, dmin, dmax)
    rs = RuleSet.model_validate(ruleset_raw) if ruleset_raw else RuleSet()
    hook_raw = clip.get("hook_end")
    hook_bounded = hook_end_in_clip(hook_raw, length)
    hook_ok: bool | None = None
    hook_s = 1.0
    if rs.hook.required:
        if hook_bounded is None:
            hook_ok = False
            hook_s = 0.4
        elif rs.hook.max_seconds is not None and hook_bounded > float(rs.hook.max_seconds) + 0.05:
            hook_ok = False
            hook_s = 0.3
        else:
            hook_ok = True
            hook_s = 1.0
    window_text = segment_text(_asset_ns(asset), start, end)
    title = str(clip.get("title") or "")
    caption = str(clip.get("caption") or "")
    ost = str(clip.get("on_screen_text") or "")
    copy_checks = check_copy(rs, "youtube", title, caption, extra_text=f"{ost}\n{window_text}")
    copy_fails = [c for c in copy_checks if c.get("status") == "fail"]
    prohibited = any(c.get("rule") == "prohibitions.terms" for c in copy_fails)
    copy_ok = not copy_fails
    if prohibited:
        score = 0.0
    else:
        score = 0.45 * dur_s + 0.30 * hook_s + 0.25 * (1.0 if copy_ok else 0.2)
        best_iou = 0.0
        for ex in similar:
            try:
                best_iou = max(
                    best_iou,
                    window_iou((start, end), (float(ex["start"]), float(ex["end"]))),
                )
            except (KeyError, TypeError, ValueError):
                continue
        score = min(1.0, score + 0.10 * best_iou)
    from app.services.publish_copy import clip_score as combined_clip_score

    subs = {k: v for k, v in parse_subscores(clip).items() if v is not None}
    if clip.get("subscores") and isinstance(clip["subscores"], dict):
        subs = {**subs, **{k: v for k, v in clip["subscores"].items() if v is not None}}
    combined = combined_clip_score(length, dmin, dmax, clip.get("score"), subs or None)
    scored = dict(clip)
    scored.update({
        "start": round(start, 2),
        "end": round(end, 2),
        "duration": round(length, 2),
        "score": round(score, 4),
        "clip_score": combined,
        "subscores": subs,
        "checks": {
            "duration_ok": duration_ok,
            "hook_ok": hook_ok,
            "copy_ok": copy_ok,
            "prohibited_terms": prohibited,
            "copy_failures": [c.get("rule") for c in copy_fails],
        },
    })
    return scored


def score_clips(state: DeciderState, runtime: ShadowRuntime) -> dict[str, Any]:
    if state.get("error"):
        return {"scored_clips": [], "confidence": 0.0}
    similar = list(state.get("similar_examples") or runtime.similar_examples or [])
    scored = [
        score_one(
            clip,
            ruleset_raw=state.get("ruleset") or {},
            dmin=float(state.get("duration_min") or 15),
            dmax=float(state.get("duration_max") or 45),
            asset=state.get("asset") or {},
            similar=similar,
        )
        for clip in (state.get("proposed_clips") or [])
    ]
    confidence = round(sum(c["score"] for c in scored) / len(scored), 4) if scored else 0.0
    return {
        "scored_clips": scored,
        "confidence": confidence,
        "similar_examples": similar,
    }


def route_after_score(state: DeciderState) -> Literal["need_more_context", "decide"]:
    if state.get("error"):
        return "decide"
    if int(state.get("context_iteration") or 0) >= 1:
        return "decide"
    if not (state.get("proposed_clips") or []):
        return "decide"
    threshold = float(state.get("confidence_threshold") or 0.55)
    return "need_more_context" if float(state.get("confidence") or 0) < threshold else "decide"


def need_more_context(state: DeciderState, runtime: ShadowRuntime) -> dict[str, Any]:
    """Read-only: candidates/clips from other campaigns. At most one iteration."""
    if runtime.similar_examples is not None:
        return {
            "similar_examples": list(runtime.similar_examples),
            "context_iteration": 1,
        }
    snaps = runtime.snapshots or {}
    if "similar" in snaps:
        return {"similar_examples": list(snaps["similar"]), "context_iteration": 1}

    db = wrap_db(runtime.db)
    if db is None:
        return {"similar_examples": [], "context_iteration": 1}

    from app.models.candidate import Candidate

    campaign_id = int(state.get("campaign_id") or 0)
    rows = (
        db.query(Candidate)
        .filter(Candidate.campaign_id != campaign_id)
        .filter(Candidate.status.in_(("approved", "rendered", "pending")))
        .order_by(Candidate.score.desc().nullslast())
        .limit(8)
        .all()
    )
    examples = []
    for c in rows:
        extra = c.extra_metadata or {}
        examples.append({
            "kind": "candidate",
            "campaign_id": c.campaign_id,
            "start": float(c.start_time),
            "end": float(c.end_time),
            "score": float(c.score or 0),
            "title": extra.get("title") or "",
        })
    return {"similar_examples": examples, "context_iteration": 1}


def decide(state: DeciderState, runtime: ShadowRuntime) -> dict[str, Any]:
    opts = decider_options(
        _campaign_ns(state.get("campaign") or {}),
        {"decider": state.get("decider_options") or {}},
    )
    scored = sorted(
        state.get("scored_clips") or [],
        key=lambda c: (c.get("clip_score") if c.get("clip_score") is not None else c.get("score") or 0),
        reverse=True,
    )
    picked: list[dict[str, Any]] = []
    for clip in scored:
        checks = clip.get("checks") or {}
        if checks.get("prohibited_terms"):
            continue
        if not checks.get("duration_ok"):
            continue
        if float(clip.get("score") or 0) < MIN_PICK_SCORE:
            continue
        picked.append(clip)
        if len(picked) >= opts.top_n:
            break
    if not picked:
        reason = "ningún clip pasó duración, copy y umbral de score"
        if state.get("error"):
            reason = str(state["error"])
        elif not (state.get("proposed_clips") or []):
            reason = "el LLM no propuso clips"
        return {
            "chosen_clips": [],
            "decision": "none",
            "decision_reason": reason,
        }
    return {
        "chosen_clips": picked,
        "decision": "clips",
        "decision_reason": f"elegidos {len(picked)} clip(s) con score >= {MIN_PICK_SCORE}",
    }


def _stake_usd(state: DeciderState, runtime: ShadowRuntime) -> float:
    from app.services.campaign_score import campaign_rate_usd, campaign_remaining_usd

    meta = (state.get("campaign") or {}).get("source_metadata") or {}
    rate = campaign_rate_usd(meta)
    remaining = campaign_remaining_usd(meta)
    cpm_stake = rate * float(runtime.estimated_views) / 1000.0
    if rate > 0:
        return round(cpm_stake, 4)
    return round(remaining, 4)


def human_approval(state: DeciderState, runtime: ShadowRuntime) -> dict[str, Any]:
    """interrupt() when stake exceeds the threshold. Shadow records and continues."""
    from langgraph.types import interrupt

    stake = _stake_usd(state, runtime)
    threshold = float(runtime.approval_threshold)
    payload = {
        "kind": "human_approval",
        "message": "habría pedido aprobación",
        "stake_usd": stake,
        "threshold_usd": threshold,
        "estimated_views": runtime.estimated_views,
        "campaign_id": state.get("campaign_id"),
        "asset_id": state.get("asset_id"),
        "decision": state.get("decision"),
        "n_clips": len(state.get("chosen_clips") or []),
    }
    if stake <= threshold:
        return {
            "stake_usd": stake,
            "would_request_approval": False,
            "approval_note": None,
            "approval_payload": None,
        }
    if runtime.shadow or state.get("shadow", True):
        return {
            "stake_usd": stake,
            "would_request_approval": True,
            "approval_note": "habría pedido aprobación",
            "approval_payload": payload,
        }
    interrupt(payload)
    return {
        "stake_usd": stake,
        "would_request_approval": True,
        "approval_note": "approval resumed",
        "approval_payload": payload,
    }


def report(state: DeciderState, runtime: ShadowRuntime) -> dict[str, Any]:
    """Build the JSON decision. Never writes to the database."""
    calls = list(state.get("llm_calls") or [])
    iteration = int(state.get("context_iteration") or 0)
    from app.shadow.reports import infer_graph_path

    payload = {
        "asset_id": state.get("asset_id"),
        "campaign_id": state.get("campaign_id"),
        "decision": state.get("decision") or "none",
        "decision_reason": state.get("decision_reason"),
        "chosen_clips": state.get("chosen_clips") or [],
        "scored_clips": state.get("scored_clips") or [],
        "confidence": state.get("confidence"),
        "context_iteration": iteration,
        "would_request_approval": bool(state.get("would_request_approval")),
        "approval_note": state.get("approval_note"),
        "approval_payload": state.get("approval_payload"),
        "stake_usd": state.get("stake_usd"),
        "duration_window": [state.get("duration_min"), state.get("duration_max")],
        "llm_calls": calls,
        "llm_cost_usd": round(sum(float(c.get("cost_usd") or 0) for c in calls), 6),
        "llm_latency_ms": sum(int(c.get("latency_ms") or 0) for c in calls),
        "error": state.get("error"),
        "shadow": True,
        "asked_more_context": iteration >= 1,
        "graph_path": infer_graph_path({"graph": {"context_iteration": iteration}}),
    }
    return {"report": payload}
