"""Clip-decider helpers: chunk a full transcript, snap edges, merge by IoU.

The live tick and the shadow graph share this so a 30+ minute asset is not
truncated to the first 80 segments / 5 000 characters.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

# Hard cap on the transcript block of one prompt (same ceiling as the old
# live `[:5000]` slice). chunk_chars may be smaller; never larger.
MAX_SEGMENT_BLOCK_CHARS = 5000
LEGACY_SEGMENT_LIMIT = 80
DEFAULT_CHUNK_CHARS = 4000
DEFAULT_CANDIDATES_PER_CHUNK = 3
DEFAULT_TOP_N = 8
DEFAULT_OVERLAP = 0.10
DEFAULT_IOU_THRESHOLD = 0.5
DEFAULT_PHRASE_SHIFT = 2.0

_SENTENCE_END = re.compile(r"[.!?…]+[\"'»”’)]*$")


@dataclass(frozen=True)
class DeciderOptions:
    """Knobs mirrored in render_spec.decider (VPS) and the worker payload."""

    chunk_chars: int = DEFAULT_CHUNK_CHARS
    candidates_per_chunk: int = DEFAULT_CANDIDATES_PER_CHUNK
    top_n: int = DEFAULT_TOP_N
    overlap: float = DEFAULT_OVERLAP
    iou_threshold: float = DEFAULT_IOU_THRESHOLD
    phrase_shift: float = DEFAULT_PHRASE_SHIFT

    def as_dict(self) -> dict[str, float | int]:
        return {
            "chunk_chars": int(self.chunk_chars),
            "candidates_per_chunk": int(self.candidates_per_chunk),
            "top_n": int(self.top_n),
            "overlap": float(self.overlap),
        }


def _int(raw: Any, default: int, *, minimum: int = 0) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, value)


def _float(raw: Any, default: float, *, minimum: float = 0.0, maximum: float | None = None) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def decider_options(campaign: Any = None, spec: Mapping[str, Any] | None = None) -> DeciderOptions:
    """Read ``decider.*`` from a render_spec payload or campaign metadata.

    Missing keys keep the new full-video defaults. ``chunk_chars=0`` restores
    the old single window (first 80 segments, 5 000 characters).
    """
    raw: dict[str, Any] = {}
    if campaign is not None:
        meta = getattr(campaign, "source_metadata", None) or {}
        if isinstance(meta, dict):
            if isinstance(meta.get("decider"), dict):
                raw.update(meta["decider"])
            rs = meta.get("ruleset")
            if isinstance(rs, dict) and isinstance(rs.get("decider"), dict):
                raw.update(rs["decider"])
        camp_spec = getattr(campaign, "spec", None) or {}
        if isinstance(camp_spec, dict) and isinstance(camp_spec.get("decider"), dict):
            raw.update(camp_spec["decider"])
    if isinstance(spec, Mapping) and isinstance(spec.get("decider"), dict):
        raw.update(spec["decider"])
    chunk_chars = _int(raw.get("chunk_chars"), DEFAULT_CHUNK_CHARS, minimum=0)
    if chunk_chars > MAX_SEGMENT_BLOCK_CHARS:
        chunk_chars = MAX_SEGMENT_BLOCK_CHARS
    return DeciderOptions(
        chunk_chars=chunk_chars,
        candidates_per_chunk=_int(
            raw.get("candidates_per_chunk"), DEFAULT_CANDIDATES_PER_CHUNK, minimum=1
        ),
        top_n=_int(raw.get("top_n"), DEFAULT_TOP_N, minimum=1),
        overlap=_float(raw.get("overlap"), DEFAULT_OVERLAP, minimum=0.0, maximum=0.9),
        iou_threshold=_float(
            raw.get("iou_threshold"), DEFAULT_IOU_THRESHOLD, minimum=0.0, maximum=1.0
        ),
        phrase_shift=_float(raw.get("phrase_shift"), DEFAULT_PHRASE_SHIFT, minimum=0.0),
    )


def segment_lines(segs: Sequence[Any] | None, limit: int | None = None) -> list[str]:
    """Format transcript segments the way the live decider prompt expects."""
    lines: list[str] = []
    items = list(segs or [])
    if limit is not None:
        items = items[:limit]
    for s in items:
        if not isinstance(s, dict):
            continue
        lines.append(
            f"{s.get('start', 0):.1f}-{s.get('end', 0):.1f} {(s.get('text') or '').strip()}"
        )
    return lines


def _seg_start(seg: Mapping[str, Any]) -> float:
    try:
        return float(seg.get("start") or 0)
    except (TypeError, ValueError):
        return 0.0


def _seg_end(seg: Mapping[str, Any]) -> float:
    try:
        return float(seg.get("end") or 0)
    except (TypeError, ValueError):
        return 0.0


def chunk_segments(
    segments: Sequence[Any] | None,
    options: DeciderOptions | None = None,
    *,
    chunk_chars: int | None = None,
    overlap: float | None = None,
) -> list[dict[str, Any]]:
    """Split ``segments`` into overlapping transcript chunks.

    ``chunk_chars=0`` is the legacy path: first 80 segments, then a 5 000
    character slice, one chunk. Otherwise the whole transcript is covered.
    """
    opts = options or DeciderOptions()
    size = opts.chunk_chars if chunk_chars is None else int(chunk_chars)
    ov = opts.overlap if overlap is None else float(overlap)
    segs = [s for s in (segments or []) if isinstance(s, dict)]
    if not segs:
        return []
    if size <= 0:
        segs = segs[:LEGACY_SEGMENT_LIMIT]
        lines = segment_lines(segs)
        block = "\n".join(lines)[:MAX_SEGMENT_BLOCK_CHARS]
        used = _segments_covering_block(segs, lines, block)
        return [_chunk_dict(used, block, 0)]

    size = min(max(1, size), MAX_SEGMENT_BLOCK_CHARS)
    lines = segment_lines(segs)
    n = len(segs)
    chunks: list[dict[str, Any]] = []
    i = 0
    while i < n:
        chars = 0
        j = i
        while j < n:
            add = len(lines[j]) + (1 if j > i else 0)
            if chars + add > size and j > i:
                break
            chars += add
            j += 1
        block = "\n".join(lines[i:j])[:MAX_SEGMENT_BLOCK_CHARS]
        chunks.append(_chunk_dict(segs[i:j], block, len(chunks)))
        if j >= n:
            break
        step = max(1, int(round((j - i) * (1.0 - ov))))
        nxt = i + step
        if nxt <= i:
            nxt = i + 1
        if nxt >= j:
            nxt = j
        i = nxt
    return chunks


def _segments_covering_block(
    segs: list[dict[str, Any]], lines: list[str], block: str
) -> list[dict[str, Any]]:
    used: list[dict[str, Any]] = []
    acc = ""
    for seg, line in zip(segs, lines):
        nxt = line if not acc else acc + "\n" + line
        if len(nxt) > len(block) and used:
            break
        acc = nxt[: len(block)]
        used.append(seg)
        if len(acc) >= len(block):
            break
    return used or segs[:1]


def _chunk_dict(segs: list[dict[str, Any]], block: str, index: int) -> dict[str, Any]:
    return {
        "index": index,
        "segments": segs,
        "block": block,
        "start": _seg_start(segs[0]) if segs else 0.0,
        "end": _seg_end(segs[-1]) if segs else 0.0,
        "prompt_chars": len(block),
    }


def window_iou(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    union = max(a[1], b[1]) - min(a[0], b[0])
    return inter / union if union else 0.0


def merge_by_iou(
    clips: Sequence[Mapping[str, Any]],
    *,
    threshold: float = DEFAULT_IOU_THRESHOLD,
    score_key: str = "score",
) -> list[dict[str, Any]]:
    """Keep higher-scoring windows; drop later ones whose IoU is too high."""
    ranked = sorted(
        (dict(c) for c in clips if isinstance(c, Mapping)),
        key=lambda c: float(c.get(score_key) or 0),
        reverse=True,
    )
    kept: list[dict[str, Any]] = []
    for clip in ranked:
        try:
            window = (float(clip.get("start") or 0), float(clip.get("end") or 0))
        except (TypeError, ValueError):
            continue
        if window[1] <= window[0]:
            continue
        if any(
            window_iou(window, (float(k.get("start") or 0), float(k.get("end") or 0)))
            >= threshold
            for k in kept
        ):
            continue
        kept.append(clip)
    return kept


def take_top_n(clips: Sequence[Mapping[str, Any]], n: int, *, score_key: str = "score") -> list[dict[str, Any]]:
    ranked = sorted(
        (dict(c) for c in clips),
        key=lambda c: float(c.get(score_key) or 0),
        reverse=True,
    )
    return ranked[: max(1, int(n))]


def parse_subscores(clip: Mapping[str, Any] | None) -> dict[str, float | None]:
    """Pull hook/flow/value/fit from a model clip (top-level, nested, or extra)."""
    raw = clip or {}
    nested = raw.get("subscores") if isinstance(raw.get("subscores"), Mapping) else {}
    extra = raw.get("extra") if isinstance(raw.get("extra"), Mapping) else {}
    extra_ss = extra.get("subscores") if isinstance(extra.get("subscores"), Mapping) else {}
    out: dict[str, float | None] = {}
    for key in ("hook", "flow", "value", "fit"):
        out[key] = _unit_score(
            raw.get(key, nested.get(key, extra_ss.get(key, extra.get(key))))
        )
    return out


def _unit_score(raw: Any) -> float | None:
    if raw is None or isinstance(raw, bool) or raw == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value != value:  # NaN
        return None
    return min(1.0, max(0.0, value))


def iter_words(segments: Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    """WhisperX word timings, falling back to the segment as one token."""
    words: list[dict[str, Any]] = []
    for seg in segments or []:
        if not isinstance(seg, Mapping):
            continue
        raw_words = seg.get("words") or []
        if isinstance(raw_words, list) and raw_words:
            for w in raw_words:
                if not isinstance(w, Mapping):
                    continue
                token = _word_token(w)
                if token is not None:
                    words.append(token)
            continue
        token = _word_token(
            {
                "start": seg.get("start"),
                "end": seg.get("end"),
                "word": str(seg.get("text") or "").strip(),
            }
        )
        if token is not None:
            words.append(token)
    words.sort(key=lambda w: (w["start"], w["end"]))
    _mark_sentence_bounds(words)
    return words


def _word_token(w: Mapping[str, Any]) -> dict[str, Any] | None:
    text = str(w.get("word") or w.get("text") or "").strip()
    try:
        start = float(w["start"])
        end = float(w["end"])
    except (KeyError, TypeError, ValueError):
        return None
    if end < start:
        return None
    return {
        "start": start,
        "end": end,
        "word": text,
        "sentence_end": bool(text) and bool(_SENTENCE_END.search(text)),
        "sentence_start": False,
    }


def _mark_sentence_bounds(words: list[dict[str, Any]]) -> None:
    prev_end = True
    for w in words:
        w["sentence_start"] = prev_end
        prev_end = bool(w.get("sentence_end"))


def snap_window(
    start: float,
    end: float,
    segments: Sequence[Mapping[str, Any]] | None,
    *,
    max_shift: float = DEFAULT_PHRASE_SHIFT,
) -> tuple[float, float]:
    """Move ``start``/``end`` onto a word (prefer a sentence boundary).

    Walks at most ``max_shift`` seconds to the previous sentence start / next
    sentence end. With no word timings the original window is returned.
    """
    try:
        start_f = float(start)
        end_f = float(end)
    except (TypeError, ValueError):
        return start, end
    if end_f <= start_f:
        return round(start_f, 2), round(end_f, 2)
    words = iter_words(segments)
    if not words:
        return round(start_f, 2), round(end_f, 2)
    new_start = _snap_start(start_f, words, max_shift)
    new_end = _snap_end(end_f, words, max_shift)
    if new_end <= new_start:
        return round(start_f, 2), round(end_f, 2)
    return round(new_start, 2), round(new_end, 2)


def _snap_start(t: float, words: list[dict[str, Any]], max_shift: float) -> float:
    idx = 0
    for i, w in enumerate(words):
        if w["end"] < t:
            idx = i
            continue
        idx = i
        break
    chosen = words[idx]
    start = float(chosen["start"])
    if chosen.get("sentence_start") or max_shift <= 0:
        return start
    for j in range(idx, -1, -1):
        w = words[j]
        if t - w["start"] > max_shift:
            break
        if w.get("sentence_start"):
            return float(w["start"])
    return start


def _snap_end(t: float, words: list[dict[str, Any]], max_shift: float) -> float:
    idx = len(words) - 1
    for i, w in enumerate(words):
        if w["end"] >= t:
            idx = i
            break
        idx = i
    chosen = words[idx]
    end = float(chosen["end"])
    if chosen.get("sentence_end") or max_shift <= 0:
        return end
    for j in range(idx, len(words)):
        w = words[j]
        if w["end"] - t > max_shift:
            break
        if w.get("sentence_end"):
            return float(w["end"])
    return end


def prompt_is_within_limit(prompt: str, *, segment_block: str) -> bool:
    """True when the transcript block of ``prompt`` stays under the hard cap."""
    return len(segment_block) <= MAX_SEGMENT_BLOCK_CHARS
