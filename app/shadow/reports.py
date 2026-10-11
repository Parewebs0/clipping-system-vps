"""Read shadow JSONL reports from disk. Never opens a database session."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
_BASE_PATH = [
    "load_context",
    "extract_rules",
    "propose_clips",
    "score_clips",
]
_TAIL_PATH = ["decide", "human_approval", "report"]


def reports_dir() -> Path:
    env = (os.environ.get("SHADOW_REPORTS_DIR") or "").strip()
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "reports" / "shadow"


def infer_graph_path(row: dict[str, Any]) -> list[str]:
    """Nodes visited. Reconstructs from context_iteration for older JSONL."""
    graph = row.get("graph") if isinstance(row.get("graph"), dict) else {}
    for candidate in (row.get("graph_path"), graph.get("graph_path")):
        if isinstance(candidate, list) and candidate:
            return [str(x) for x in candidate]
    path = list(_BASE_PATH)
    iteration = int(graph.get("context_iteration") or row.get("context_iteration") or 0)
    if iteration >= 1:
        path += ["need_more_context", "score_clips"]
    path += _TAIL_PATH
    return path


def approval_reason(graph: dict[str, Any]) -> str | None:
    if not graph.get("would_request_approval"):
        return None
    note = str(graph.get("approval_note") or "habría pedido aprobación")
    stake = graph.get("stake_usd")
    payload = graph.get("approval_payload") if isinstance(graph.get("approval_payload"), dict) else {}
    threshold = payload.get("threshold_usd")
    views = payload.get("estimated_views")
    bits = [note]
    if stake is not None:
        bits.append(f"stake ${float(stake):.2f}")
    if threshold is not None:
        bits.append(f"umbral ${float(threshold):.2f}")
    if views is not None:
        bits.append(f"{views:g} vistas est.")
    return " · ".join(bits)


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _i(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            rows.append(obj)
    return rows


def summarize(run_id: str, path: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    ious = [_f((r.get("diff") or {}).get("iou")) for r in rows]
    mean_iou = sum(ious) / n if n else 0.0
    cost = 0.0
    latency = 0
    n_approval = 0
    generated = None
    for r in rows:
        cur = r.get("current") if isinstance(r.get("current"), dict) else {}
        graph = r.get("graph") if isinstance(r.get("graph"), dict) else {}
        cost += _f(cur.get("cost_usd")) + _f(graph.get("cost_usd"))
        latency += _i(cur.get("latency_ms")) + _i(graph.get("latency_ms"))
        if graph.get("would_request_approval"):
            n_approval += 1
        generated = r.get("generated_at") or generated
    return {
        "id": run_id,
        "filename": path.name,
        "generated_at": generated,
        "n_assets": n,
        "match_pct": round(mean_iou * 100.0, 1),
        "mean_iou": round(mean_iou, 4),
        "cost_usd_total": round(cost, 6),
        "latency_ms_total": latency,
        "n_would_request_approval": n_approval,
    }


def _side(block: dict[str, Any]) -> dict[str, Any]:
    windows = block.get("windows") or []
    clean_windows = []
    for w in windows:
        if isinstance(w, (list, tuple)) and len(w) >= 2:
            clean_windows.append([_f(w[0]), _f(w[1])])
    return {
        "n_clips": _i(block.get("n_clips"), len(clean_windows)),
        "windows": clean_windows,
        "titles": [str(t) for t in (block.get("titles") or [])],
        "captions": [str(t) for t in (block.get("captions") or [])],
        "cost_usd": _f(block.get("cost_usd")),
        "latency_ms": _i(block.get("latency_ms")),
        "decision": block.get("decision"),
        "decision_reason": block.get("decision_reason"),
        "confidence": block.get("confidence"),
    }


def asset_view(row: dict[str, Any]) -> dict[str, Any]:
    current = row.get("current") if isinstance(row.get("current"), dict) else {}
    graph = row.get("graph") if isinstance(row.get("graph"), dict) else {}
    diff = row.get("diff") if isinstance(row.get("diff"), dict) else {}
    iteration = _i(graph.get("context_iteration"))
    asked = iteration >= 1
    path = infer_graph_path(row)
    return {
        "asset_id": str(row.get("asset_id") or ""),
        "campaign_id": row.get("campaign_id"),
        "label": row.get("label"),
        "generated_at": row.get("generated_at"),
        "current": _side(current),
        "graph": {
            **_side(graph),
            "would_request_approval": bool(graph.get("would_request_approval")),
            "approval_note": graph.get("approval_note"),
            "approval_reason": approval_reason(graph),
            "stake_usd": graph.get("stake_usd"),
            "context_iteration": iteration,
            "asked_more_context": asked,
            "graph_path": path,
        },
        "diff": {
            "iou": _f(diff.get("iou")),
            "title_similarity": _f(diff.get("title_similarity")),
            "n_clips_delta": _i(diff.get("n_clips_delta")),
        },
    }


def list_runs() -> list[dict[str, Any]]:
    root = reports_dir()
    if not root.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.jsonl")):
        if not SAFE_ID.match(path.stem):
            continue
        rows = load_rows(path)
        if not rows:
            continue
        out.append(summarize(path.stem, path, rows))
    out.sort(key=lambda r: r.get("generated_at") or r["id"], reverse=True)
    return out


def get_run(run_id: str) -> dict[str, Any] | None:
    if not SAFE_ID.match(run_id):
        return None
    root = reports_dir().resolve()
    path = (root / f"{run_id}.jsonl").resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    if not path.is_file():
        return None
    rows = load_rows(path)
    summary = summarize(run_id, path, rows)
    return {**summary, "assets": [asset_view(r) for r in rows]}
