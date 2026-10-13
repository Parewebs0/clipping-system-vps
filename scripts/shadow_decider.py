#!/usr/bin/env python3
"""Run the live clip-decider (prompt+parse, no writes) and the LangGraph shadow
graph on the same assets. Writes JSONL + Markdown. Never commits to the DB.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.decider import window_iou
from app.shadow.decider_graph.graph import run_decider
from app.shadow.decider_graph.nodes import wrap_db
from app.shadow.decider_graph.state import ShadowRuntime


def _load_current():
    path = ROOT / "scripts" / "grok_clip_decider_tick.py"
    spec = importlib.util.spec_from_file_location("grok_clip_decider_tick", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def match_iou(left: list[tuple[float, float]], right: list[tuple[float, float]]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    used = set()
    scores = []
    for a in left:
        best_i, best = -1, 0.0
        for i, b in enumerate(right):
            if i in used:
                continue
            s = window_iou(a, b)
            if s > best:
                best_i, best = i, s
        if best_i >= 0:
            used.add(best_i)
            scores.append(best)
    return round(sum(scores) / max(len(left), len(right)), 4)


def title_sim(a: list[str], b: list[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    n = max(len(a), len(b))
    total = 0.0
    for i in range(n):
        sa = a[i] if i < len(a) else ""
        sb = b[i] if i < len(b) else ""
        total += SequenceMatcher(None, sa.lower(), sb.lower()).ratio()
    return round(total / n, 4)


def _ruleset_meta(path_name: str = "ruleset_6.json") -> dict:
    p = ROOT / "tests" / "fixtures" / "rules" / path_name
    if p.is_file():
        return {"ruleset": json.loads(p.read_text(encoding="utf-8"))}
    return {}


def _econ(rate: float, remaining: float) -> dict:
    return {
        "economics": {
            "platform_rates": {"youtube": {"rate_usd": rate}},
            "remaining_usd": remaining,
        }
    }


def sample_assets() -> list[dict[str, Any]]:
    """Five transcribed speech assets. Offline stand-in for a test DB dump."""
    rs6 = _ruleset_meta("ruleset_6.json")
    rs13 = _ruleset_meta("ruleset_13.json") or rs6
    rs18 = _ruleset_meta("ruleset_18.json") or rs6

    def segs(*pairs: tuple[float, float, str]) -> list[dict]:
        return [{"start": a, "end": b, "text": t} for a, b, t in pairs]

    speech = segs(
        (0.0, 4.0, "Watch this ForgeGUI trick for Roblox developers."),
        (4.0, 12.0, "You can generate a full shop UI in seconds with ForgeGUI."),
        (12.0, 22.0, "Here is the before and after of a messy frame."),
        (22.0, 35.0, "Subscribe if you build Roblox games and want faster GUIs."),
        (35.0, 48.0, "That is the whole workflow from prompt to published UI."),
    )
    banned = segs(
        (0.0, 5.0, "Welcome back to the casita walkthrough."),
        (5.0, 14.0, "Kanye showed up in the comments so skip this beat."),
        (14.0, 28.0, "The fold-out house is ready in about an hour."),
        (28.0, 40.0, "Tag the brand and keep the copy clean."),
    )
    short = segs(
        (0.0, 3.0, "Quick hook: the logo hits in the first second."),
        (3.0, 10.0, "Then we show the product spinning on the table."),
        (10.0, 18.0, "End on the call to action."),
    )
    long = segs(
        (0.0, 8.0, "Cold open: a creator stares at a blank canvas."),
        (8.0, 20.0, "ForgeGUI fills the layout, colors, and buttons."),
        (20.0, 34.0, "We cut to the live Roblox studio view."),
        (34.0, 50.0, "The audience sees the GUI appear without code."),
        (50.0, 70.0, "Closing recap and a pin comment about the tool."),
    )
    premium = segs(
        (0.0, 6.0, "High paying campaign intro with a sharp visual hook."),
        (6.0, 18.0, "Feature walkthrough stays inside the duration window."),
        (18.0, 32.0, "Caption mentions the product without prices."),
        (32.0, 44.0, "Outro with on-screen text for the brand."),
    )

    def with_terms(meta: dict, terms: list[str]) -> dict:
        meta = json.loads(json.dumps(meta))
        prohib = meta.setdefault("ruleset", {}).setdefault("prohibitions", {})
        prohib["terms"] = list(terms)
        return meta

    def pack(i: int, campaign: dict, duration: float, segments: list, name: str) -> dict:
        aid = str(UUID(f"{i:08x}-4a7e-41d0-8000-{i:012d}"))
        text = " ".join(s["text"] for s in segments)
        asset = {
            "id": aid,
            "campaign_id": campaign["id"],
            "duration_seconds": duration,
            "status": "transcribed",
            "extra_metadata": {"transcription": {"text": text, "segments": segments}},
        }
        return {"asset_id": aid, "asset": asset, "campaign": campaign, "name": name}

    c1 = {
        "id": 101,
        "name": "ForgeGUI clipping",
        "status": "scored",
        "source_provider": "whop",
        "source_instructions": None,
        "source_metadata": {**rs6, **_econ(0.5, 184700)},
        "spec": {},
    }
    c2 = {
        "id": 102,
        "name": "Casita walkthrough",
        "status": "scored",
        "source_provider": "whop",
        "source_instructions": None,
        "source_metadata": with_terms({**rs13, **_econ(1.2, 8000)}, ["Kanye"]),
        "spec": {},
    }
    c3 = {
        "id": 103,
        "name": "Short product spin",
        "status": "scored",
        "source_provider": "whop",
        "source_instructions": None,
        "source_metadata": {**rs18, **_econ(0.8, 2500)},
        "spec": {"duration_min": 8, "duration_max": 20},
    }
    c4 = {
        "id": 104,
        "name": "Long studio recap",
        "status": "scored",
        "source_provider": "whop",
        "source_instructions": None,
        "source_metadata": {**rs6, **_econ(0.5, 1000)},
        "spec": {},
    }
    c5 = {
        "id": 105,
        "name": "Premium high CPM",
        "status": "scored",
        "source_provider": "whop",
        "source_instructions": None,
        "source_metadata": {**rs6, **_econ(8.0, 20000)},
        "spec": {},
    }
    return [
        pack(1, c1, 90.0, speech, "forge-speech"),
        pack(2, c1, 80.0, long, "forge-long"),
        pack(3, c2, 45.0, banned, "casita-banned-term"),
        pack(4, c3, 20.0, short, "short-product"),
        pack(5, c5, 60.0, premium, "premium-cpm"),
    ]


def _is_test_db(name: str) -> bool:
    return bool(name) and (name.endswith("_test") or name.startswith("test_"))


def load_from_db(limit: int, asset_id: str | None) -> list[dict[str, Any]]:
    name = os.environ.get("CLIPPING_DB_NAME") or ""
    if not _is_test_db(name):
        raise SystemExit(
            f"refusing DB {name!r}: use CLIPPING_DB_NAME ending in _test (or --fixtures)"
        )
    from app.db.database import SessionLocal
    from app.models.asset import Asset
    from app.models.campaign import Campaign
    from app.models.candidate import Candidate
    from app.services.silent_clip_cutter import is_speech, transcript_text

    db = wrap_db(SessionLocal())
    try:
        q = db.query(Asset).filter(Asset.status == "transcribed")
        if asset_id:
            try:
                q = q.filter(Asset.id == UUID(asset_id))
            except ValueError:
                raise SystemExit(f"invalid --asset-id {asset_id}")
        assets = q.order_by(Asset.transcribed_at.asc().nullsfirst()).all()
        out = []
        for asset in assets:
            if db.query(Candidate).filter(Candidate.asset_id == asset.id).count():
                continue
            tx = (asset.extra_metadata or {}).get("transcription") or {}
            if not is_speech(transcript_text(tx)):
                continue
            campaign = db.get(Campaign, asset.campaign_id)
            packed = {
                "asset_id": str(asset.id),
                "asset": {
                    "id": str(asset.id),
                    "campaign_id": int(asset.campaign_id),
                    "duration_seconds": float(asset.duration_seconds or 0),
                    "status": asset.status,
                    "extra_metadata": dict(asset.extra_metadata or {}),
                },
                "campaign": {
                    "id": int(campaign.id) if campaign else 0,
                    "name": getattr(campaign, "name", ""),
                    "status": getattr(campaign, "status", ""),
                    "source_provider": getattr(campaign, "source_provider", "whop"),
                    "source_instructions": getattr(campaign, "source_instructions", None),
                    "source_metadata": dict(getattr(campaign, "source_metadata", None) or {}),
                    "spec": dict(getattr(campaign, "spec", None) or {}),
                },
                "name": getattr(campaign, "name", str(asset.id)),
            }
            out.append(packed)
            if len(out) >= limit:
                break
        return out
    finally:
        inner = getattr(db, "_inner", db)
        if hasattr(inner, "close"):
            inner.close()


def current_decider_on_asset(mod, packed: dict, llm) -> dict[str, Any]:
    from types import SimpleNamespace

    from app.services.decider import MAX_SEGMENT_BLOCK_CHARS, chunk_segments, decider_options
    from app.services.publish_copy import copy_rules_for_prompt
    from app.services.rules.runtime import decider_brief, duration_window, ruleset

    campaign = SimpleNamespace(**packed["campaign"])
    dmin, dmax = duration_window(campaign)
    asset = packed["asset"]
    tx = (asset.get("extra_metadata") or {}).get("transcription") or {}
    segs = tx.get("segments") or []
    duration = float(asset.get("duration_seconds") or 0) or dmax
    brief = decider_brief(campaign) or "- (none beyond duration/format)"
    rs = ruleset(campaign)
    copy_rules = copy_rules_for_prompt(rs)
    opts = decider_options(campaign)
    chunks = chunk_segments(segs, opts)
    prompt_chars = 0
    cost = 0.0
    latency_ms = 0
    model = None
    stats_last: dict[str, Any] = {}

    def _call_llm(prompt: str, meta: dict) -> dict:
        nonlocal prompt_chars, cost, latency_ms, model, stats_last
        t0 = time.monotonic()
        data, stats = llm(prompt)
        stats_last = dict(stats or {})
        prompt_chars = max(prompt_chars, len(prompt), int(meta.get("prompt_chars") or 0))
        latency_ms += int(stats_last.get("latency_ms") or (time.monotonic() - t0) * 1000)
        cost += float(stats_last.get("cost_usd") or 0)
        model = stats_last.get("model") or model
        return data if isinstance(data, dict) else {}

    reviewed = mod.collect_clips(
        segments=segs,
        dmin=dmin,
        dmax=dmax,
        duration=duration,
        brief=brief,
        copy_rules=copy_rules,
        options=opts,
        call_llm=_call_llm,
        ruleset=rs,
        retry_enabled=False,
    )
    parsed = []
    for c in reviewed:
        start = float(c.get("start") or 0)
        end = float(c.get("end") or 0)
        hook_end = mod._hook_end(c.get("hook_end"), end - start)
        extra = {
            "source": "grok_clip_decider",
            "kind": "speech",
            "title": str(c.get("title") or "")[:100],
            "caption": str(c.get("caption") or c.get("reason") or "")[:800],
            "on_screen_text": str(c.get("on_screen_text") or "")[:120],
            "subscores": dict(c.get("subscores") or {}),
        }
        if hook_end is not None:
            extra["hook_end"] = hook_end
        if c.get("copy_status") == "needs_review":
            extra["copy_status"] = c["copy_status"]
            extra["copy_reason"] = c.get("copy_reason")
        parsed.append({
            "start": round(start, 2),
            "end": round(end, 2),
            "score": float(c.get("score") or 0),
            "reason": str(c.get("reason") or "grok")[:500],
            "extra": extra,
        })
    if not prompt_chars:
        prompt_chars = sum(min(len(ch["block"]), MAX_SEGMENT_BLOCK_CHARS) for ch in chunks)
    return {
        "clips": parsed,
        "prompt_chars": prompt_chars,
        "latency_ms": latency_ms,
        "cost_usd": cost,
        "model": model,
        "duration_window": [dmin, dmax],
        "n_chunks": len(chunks),
    }


def make_llm(packed: dict, mock: bool):
    from app.shadow.decider_graph.nodes import heuristic_propose, _call_llm
    from app.shadow.decider_graph.state import ShadowRuntime

    asset = packed["asset"]
    tx = (asset.get("extra_metadata") or {}).get("transcription") or {}
    segs = [s for s in (tx.get("segments") or []) if isinstance(s, dict)]

    def _llm(prompt: str, schema=None):
        if mock or not (os.environ.get("XAI_API_KEY") or ""):
            from types import SimpleNamespace
            from app.services.rules.runtime import duration_window

            camp = SimpleNamespace(**packed["campaign"])
            dmin, dmax = duration_window(camp)
            duration = float(asset.get("duration_seconds") or 0) or dmax
            clips = heuristic_propose(segs, dmin, dmax, duration)
            return {"clips": clips}, {
                "model": "heuristic",
                "cost_usd": 0.0,
                "latency_ms": 1,
                "prompt_tokens": 0,
                "completion_tokens": 0,
            }
        return _call_llm(prompt, ShadowRuntime())

    return _llm


def compare_row(packed: dict, current: dict, graph_state: dict) -> dict[str, Any]:
    report = graph_state.get("report") or {}
    cur_clips = current.get("clips") or []
    graph_clips = report.get("chosen_clips") or []
    cur_w = [(c["start"], c["end"]) for c in cur_clips]
    g_w = [(c["start"], c["end"]) for c in graph_clips]
    cur_titles = [((c.get("extra") or {}).get("title") or "") for c in cur_clips]
    g_titles = [c.get("title") or "" for c in graph_clips]
    cur_caps = [((c.get("extra") or {}).get("caption") or "") for c in cur_clips]
    g_caps = [c.get("caption") or "" for c in graph_clips]
    from app.shadow.reports import approval_reason, infer_graph_path

    graph_block = {
        "n_clips": len(graph_clips),
        "windows": g_w,
        "titles": g_titles,
        "captions": g_caps,
        "decision": report.get("decision"),
        "decision_reason": report.get("decision_reason"),
        "confidence": report.get("confidence"),
        "cost_usd": report.get("llm_cost_usd"),
        "latency_ms": report.get("llm_latency_ms"),
        "would_request_approval": report.get("would_request_approval"),
        "approval_note": report.get("approval_note"),
        "approval_payload": report.get("approval_payload"),
        "stake_usd": report.get("stake_usd"),
        "context_iteration": report.get("context_iteration"),
    }
    graph_block["asked_more_context"] = int(graph_block.get("context_iteration") or 0) >= 1
    graph_block["graph_path"] = infer_graph_path({"graph": graph_block})
    graph_block["approval_reason"] = approval_reason(graph_block)
    return {
        "asset_id": packed["asset_id"],
        "campaign_id": packed["campaign"]["id"],
        "label": packed.get("name"),
        "current": {
            "n_clips": len(cur_clips),
            "windows": cur_w,
            "titles": cur_titles,
            "captions": cur_caps,
            "cost_usd": current.get("cost_usd"),
            "latency_ms": current.get("latency_ms"),
            "model": current.get("model"),
        },
        "graph": graph_block,
        "diff": {
            "iou": match_iou(cur_w, g_w),
            "title_similarity": title_sim(cur_titles, g_titles),
            "n_clips_delta": len(graph_clips) - len(cur_clips),
            "cost_usd_delta": round(
                float(report.get("llm_cost_usd") or 0) - float(current.get("cost_usd") or 0), 6
            ),
            "latency_ms_delta": int(report.get("llm_latency_ms") or 0) - int(current.get("latency_ms") or 0),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def markdown_summary(rows: list[dict], out_jsonl: Path) -> str:
    n = len(rows)
    iou = sum(r["diff"]["iou"] for r in rows) / n if n else 0
    titles = sum(r["diff"]["title_similarity"] for r in rows) / n if n else 0
    cost_c = sum(float(r["current"]["cost_usd"] or 0) for r in rows)
    cost_g = sum(float(r["graph"]["cost_usd"] or 0) for r in rows)
    lat_c = sum(int(r["current"]["latency_ms"] or 0) for r in rows)
    lat_g = sum(int(r["graph"]["latency_ms"] or 0) for r in rows)
    lines = [
        "# Shadow clip-decider report",
        "",
        f"Generated `{datetime.now(timezone.utc).isoformat()}`. Source: `{out_jsonl.name}`.",
        "",
        f"- assets: **{n}**",
        f"- mean window IoU: **{iou:.3f}**",
        f"- mean title similarity: **{titles:.3f}**",
        f"- LLM cost current vs graph: **${cost_c:.6f}** / **${cost_g:.6f}**",
        f"- LLM latency current vs graph: **{lat_c} ms** / **{lat_g} ms**",
        "",
        "| asset | campaign | n current | n graph | IoU | titles | approval |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for r in rows:
        note = r["graph"].get("approval_note") or ""
        lines.append(
            f"| `{r['asset_id'][:8]}` | {r.get('label') or r['campaign_id']} | "
            f"{r['current']['n_clips']} | {r['graph']['n_clips']} | "
            f"{r['diff']['iou']:.2f} | {r['diff']['title_similarity']:.2f} | {note or '—'} |"
        )
    lines += ["", "Zero DB writes. Graph `shadow=True`."]
    return "\n".join(lines) + "\n"


def main() -> int:
    p = argparse.ArgumentParser(description="Shadow LangGraph clip-decider comparison")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--asset-id", default=None)
    p.add_argument("--out", default=None, help="JSONL path (default reports/shadow/<date>.jsonl)")
    p.add_argument("--from-db", action="store_true", help="Read transcribed assets from a *_test DB")
    p.add_argument("--fixtures", action="store_true", default=False)
    p.add_argument("--mock-llm", action="store_true")
    args = p.parse_args()
    if not args.from_db:
        args.fixtures = True
    mock = args.mock_llm or not (os.environ.get("XAI_API_KEY") or "")

    day = date.today().isoformat()
    out = Path(args.out) if args.out else ROOT / "reports" / "shadow" / f"{day}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    md_path = out.with_suffix(".md")

    if args.from_db:
        packed_list = load_from_db(args.limit, args.asset_id)
    else:
        packed_list = sample_assets()
        if args.asset_id:
            packed_list = [x for x in packed_list if x["asset_id"] == args.asset_id]
        packed_list = packed_list[: args.limit]

    if not packed_list:
        print("no assets", file=sys.stderr)
        return 1

    mod = _load_current()
    rows = []
    for packed in packed_list:
        llm = make_llm(packed, mock=mock)
        t0 = time.monotonic()
        current = current_decider_on_asset(mod, packed, llm)
        current_ms = int((time.monotonic() - t0) * 1000)
        current["wall_ms"] = current_ms

        runtime = ShadowRuntime(
            db=None,
            llm=llm if not mock else None,
            shadow=True,
            snapshots={packed["asset_id"]: packed, "campaign": packed["campaign"]},
            confidence_threshold=float(os.environ.get("SHADOW_CONFIDENCE_THRESHOLD", "0.55")),
            approval_threshold=float(os.environ.get("SHADOW_APPROVAL_THRESHOLD", "50")),
            estimated_views=float(os.environ.get("SHADOW_ESTIMATED_VIEWS", "10000")),
        )
        t1 = time.monotonic()
        graph_state = run_decider(packed["asset_id"], runtime)
        graph_ms = int((time.monotonic() - t1) * 1000)
        row = compare_row(packed, current, graph_state)
        row["graph"]["wall_ms"] = graph_ms
        row["current"]["wall_ms"] = current_ms
        rows.append(row)
        print(
            f"asset={packed['asset_id'][:8]} current={row['current']['n_clips']} "
            f"graph={row['graph']['n_clips']} iou={row['diff']['iou']}"
        )

    with out.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    md = markdown_summary(rows, out)
    md_path.write_text(md, encoding="utf-8")
    print(f"wrote {out} and {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
