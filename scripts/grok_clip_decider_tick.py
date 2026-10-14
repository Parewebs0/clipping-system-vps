#!/usr/bin/env python3
"""Speech clip-decider. One Grok JSON call per transcript chunk of an asset.

The full WhisperX transcript is chunked (see ``decider.chunk_chars``) with
overlap, 3–4 windows per chunk, then merged by IoU. At most
``decider.max_chunks`` windows are scored per asset (default 12); a longer
split is sampled evenly, first and last included. Captions are checked
with verifier.check_copy; a failure is retried once per chunk. A second
failure stores the candidate as needs_review. The score is clip_score from
hook/flow/value/fit sub-scores (duration fit if those are missing)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path("/opt/clipping-system")
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

PROMPT = """Pick up to {n} clip windows from this transcript chunk for short-form video.
Rules: duration_min={dmin}, duration_max={dmax} seconds. Format 9:16.
Campaign rules (must be respected):
{brief}
Copy rules for the title and the caption (must be respected):
{copy_rules}
Return JSON:
{{"clips":[{{"start":0.0,"end":20.0,"reason":"why this window","hook":0.82,"flow":0.70,"value":0.75,"fit":0.80,"title":"max 80 chars hook","caption":"the full post caption that satisfies the copy rules above","on_screen_text":"short overlay text or empty","hook_end":1.5}}]}}
hook, flow, value and fit are 0-1 sub-scores for THIS window. Score each clip on its own. Do not reuse one number for every clip.
hook_end is optional: seconds from the clip start where the hook ends. Omit the key when you are not sure. Do not guess.
Prefer complete phrases. Do not exceed duration_max. Stay inside video_duration={duration}.
This is chunk {chunk}/{chunks} covering {chunk_start:.1f}-{chunk_end:.1f}s of the video. Only pick windows inside this chunk.
Transcript segments (start,end,text) for this chunk only:
"""


def _fmt(value: str) -> str:
    """Keep campaign text from breaking str.format braces."""
    return value.replace("{", "{{").replace("}", "}}")


def build_decider_prompt(
    *,
    dmin,
    dmax,
    duration,
    brief: str,
    copy_rules: str,
    n: int = 3,
    chunk: int = 1,
    chunks: int = 1,
    chunk_start: float = 0.0,
    chunk_end: float = 0.0,
) -> str:
    return PROMPT.format(
        n=int(n),
        dmin=dmin,
        dmax=dmax,
        duration=duration,
        brief=_fmt(brief),
        copy_rules=_fmt(copy_rules),
        chunk=int(chunk),
        chunks=int(chunks),
        chunk_start=float(chunk_start),
        chunk_end=float(chunk_end) if chunk_end else float(duration or 0),
    )


def retry_prompt(base: str, failures: str) -> str:
    """Second call: the same prompt plus the copy checks that failed."""
    return (
        base
        + "\nThe previous JSON failed the copy checks. Fix the title, caption and on_screen_text. "
        + "Keep the same start and end.\nFailures:\n"
        + failures
        + "\nReturn the full JSON again.\n"
    )


def prepare_clips(clips, dmin: float, dmax: float, limit: int | None = None) -> list[dict]:
    """Keep the windows the decider will store. Same duration bounds as before."""
    prepared = []
    items = list(clips or [])
    if limit is not None:
        items = items[: max(1, int(limit))]
    for clip in items:
        if not isinstance(clip, dict):
            continue
        try:
            start = float(clip.get("start") or 0)
            end = float(clip.get("end") or 0)
        except (TypeError, ValueError):
            continue
        if end - start < max(3.0, dmin * 0.5):
            continue
        if end - start > dmax + 1:
            end = start + dmax
        item = dict(clip)
        item["start"] = start
        item["end"] = end
        prepared.append(item)
    return prepared


def _hook_end(raw, duration: float | None = None) -> float | None:
    """Optional decider field. Non-numeric, <= 0, or past the clip is dropped (#65)."""
    if raw is None or raw == "":
        return None
    try:
        value = round(float(raw), 2)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    if duration is not None:
        try:
            limit = float(duration)
        except (TypeError, ValueError):
            return None
        if value > limit:
            return None
    return value


def segment_lines(segs, limit: int | None = None) -> list[str]:
    """Format transcript segments the way the live decider prompt expects."""
    from app.services.decider import segment_lines as _lines

    return _lines(segs, limit)


def build_prompt(*, dmin: float, dmax: float, duration: float, brief: str, segments) -> str:
    """Pure prompt builder. Slice applies to the segment block of one chunk."""
    from app.services.decider import MAX_SEGMENT_BLOCK_CHARS, chunk_segments

    brief = brief or "- (none beyond duration/format)"
    chunks = chunk_segments(segments)
    block = chunks[0]["block"] if chunks else ""
    return build_decider_prompt(
        dmin=dmin, dmax=dmax, duration=duration, brief=brief, copy_rules="",
        chunk_end=duration,
    ) + block[:MAX_SEGMENT_BLOCK_CHARS]


def parse_clips(data, dmin: float, dmax: float, limit: int | None = None) -> list[dict]:
    """Pure parse of a Grok JSON payload into candidate fields. No DB writes."""
    from app.services.decider import parse_subscores
    from app.services.publish_copy import clip_score

    clips = data.get("clips") if isinstance(data, dict) else None
    if not clips:
        return []
    out: list[dict] = []
    items = list(clips)
    if limit is not None:
        items = items[: max(1, int(limit))]
    for clip in items:
        start = float(clip.get("start") or 0)
        end = float(clip.get("end") or 0)
        if end - start < max(3.0, dmin * 0.5):
            continue
        if end - start > dmax + 1:
            end = start + dmax
        title = str(clip.get("title") or "")[:100]
        caption = str(clip.get("caption") or clip.get("reason") or "")[:800]
        subs = parse_subscores(clip)
        extra = {
            "source": "grok_clip_decider",
            "kind": "speech",
            "title": title,
            "caption": caption,
            "on_screen_text": str(clip.get("on_screen_text") or "")[:120],
            "subscores": {k: v for k, v in subs.items() if v is not None},
        }
        hook_end = _hook_end(clip.get("hook_end"), end - start)
        if hook_end is not None:
            extra["hook_end"] = hook_end
        out.append({
            "start": round(start, 2),
            "end": round(end, 2),
            "score": clip_score(end - start, dmin, dmax, clip.get("score"), extra["subscores"] or None),
            "reason": str(clip.get("reason") or "grok")[:500],
            "extra": extra,
        })
    return out


def collect_clips(
    *,
    segments,
    dmin: float,
    dmax: float,
    duration: float,
    brief: str,
    copy_rules: str,
    options,
    call_llm,
    ruleset=None,
    retry_enabled: bool = True,
) -> list[dict]:
    """One LLM call per transcript chunk (at most ``options.max_chunks``), then IoU merge and phrase snap."""
    from app.services.decider import (
        MAX_SEGMENT_BLOCK_CHARS,
        chunk_segments,
        limit_chunks,
        merge_by_iou,
        parse_subscores,
        snap_window,
        take_top_n,
    )
    from app.services.publish_copy import clip_score, review_clip_copies

    chunks = limit_chunks(chunk_segments(segments, options), options)
    n_chunks = max(1, len(chunks))
    collected: list[dict] = []
    for ch in chunks:
        block = ch["block"][:MAX_SEGMENT_BLOCK_CHARS]
        prompt = build_decider_prompt(
            dmin=dmin,
            dmax=dmax,
            duration=duration,
            brief=brief,
            copy_rules=copy_rules,
            n=options.candidates_per_chunk,
            chunk=ch["index"] + 1,
            chunks=n_chunks,
            chunk_start=ch["start"],
            chunk_end=ch["end"],
        ) + block
        meta = {
            "chunk": ch["index"],
            "n_chunks": n_chunks,
            "prompt_chars": len(prompt),
            "segment_chars": len(block),
        }

        def _clips_of(data) -> list[dict]:
            raw = data.get("clips") if isinstance(data, dict) else None
            return prepare_clips(raw, dmin, dmax, limit=options.candidates_per_chunk)

        data = call_llm(prompt, meta)
        clips = _clips_of(data)
        if not clips:
            continue

        def _retry(failures: str, _prompt=prompt, _meta=meta) -> list[dict]:
            again = call_llm(retry_prompt(_prompt, failures), {**_meta, "retry": True})
            return _clips_of(again)

        reviewed = review_clip_copies(
            clips, ruleset, retry=_retry if retry_enabled else None
        )
        for clip in reviewed:
            start, end = snap_window(
                float(clip.get("start") or 0),
                float(clip.get("end") or 0),
                segments,
                max_shift=options.phrase_shift,
            )
            if end - start < max(3.0, dmin * 0.5):
                start = float(clip.get("start") or start)
                end = float(clip.get("end") or end)
            if end - start > dmax + 1:
                end = start + dmax
            clip["start"] = round(start, 2)
            clip["end"] = round(end, 2)
            subs = parse_subscores(clip)
            clip["subscores"] = {k: v for k, v in subs.items() if v is not None}
            clip["score"] = clip_score(
                end - start, dmin, dmax, clip.get("score"), clip["subscores"] or None
            )
            clip["chunk_index"] = ch["index"]
            collected.append(clip)
    merged = merge_by_iou(collected, threshold=options.iou_threshold)
    return take_top_n(merged, options.top_n)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=1)
    p.add_argument("--approve", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    from app.db.database import SessionLocal
    from app.models.asset import Asset
    from app.models.campaign import Campaign
    from app.models.candidate import Candidate
    from app.models.job import Job  # noqa: F401
    from app.services.decider import decider_options
    from app.services.grok_client import grok_chat_json
    from app.services.publish_copy import copy_rules_for_prompt
    from app.services.silent_clip_cutter import campaign_duration_window, is_speech, transcript_text
    from app.services.rules.runtime import decider_brief, ruleset
    from app.services.candidate_lifecycle import approve_candidate

    db = SessionLocal()
    try:
        assets = (
            db.query(Asset)
            .filter(Asset.status == "transcribed")
            .order_by(Asset.transcribed_at.asc().nullsfirst())
            .all()
        )
        done = 0
        for asset in assets:
            if db.query(Candidate).filter(Candidate.asset_id == asset.id).count():
                continue
            tx = (asset.extra_metadata or {}).get("transcription") or {}
            text = transcript_text(tx)
            if not is_speech(text):
                continue
            campaign = db.get(Campaign, asset.campaign_id)
            dmin, dmax = campaign_duration_window(campaign)
            segs = tx.get("segments") or []
            duration = float(asset.duration_seconds or 0) or dmax
            brief = decider_brief(campaign) or "- (none beyond duration/format)"
            rs = ruleset(campaign)
            copy_rules = copy_rules_for_prompt(rs)
            opts = decider_options(campaign)
            print(
                f"asset={asset.id} campaign={asset.campaign_id} calling grok "
                f"segs={len(segs)} chunk_chars={opts.chunk_chars} "
                f"n={opts.candidates_per_chunk} max_chunks={opts.max_chunks}"
            )
            if args.dry_run:
                done += 1
                if done >= args.limit:
                    break
                continue

            def _call_llm(prompt: str, meta: dict) -> dict:
                extra = {
                    "chunk_index": meta.get("chunk"),
                    "n_chunks": meta.get("n_chunks"),
                    "segment_chars": meta.get("segment_chars"),
                }
                if meta.get("retry"):
                    extra["retry"] = True
                    print(f"asset={asset.id} copy retry chunk={meta.get('chunk')}")
                return grok_chat_json(
                    prompt,
                    stage="clip_decider",
                    campaign_id=asset.campaign_id,
                    asset_id=asset.id,
                    extra=extra,
                )

            reviewed = collect_clips(
                segments=segs,
                dmin=dmin,
                dmax=dmax,
                duration=duration,
                brief=brief,
                copy_rules=copy_rules,
                options=opts,
                call_llm=_call_llm,
                ruleset=rs,
            )
            if not reviewed:
                print("no clips")
                continue
            created = []
            for clip in reviewed:
                start = float(clip.get("start") or 0)
                end = float(clip.get("end") or 0)
                title = str(clip.get("title") or "")[:100]
                caption = str(clip.get("caption") or "")[:800]
                status = str(clip.get("copy_status") or "pending")
                extra = {
                    "source": "grok_clip_decider",
                    "kind": "speech",
                    "title": title,
                    "caption": caption,
                    "on_screen_text": str(clip.get("on_screen_text") or "")[:120],
                    "copy_attempts": int(clip.get("copy_attempts") or 1),
                    "subscores": dict(clip.get("subscores") or {}),
                    "chunk_index": clip.get("chunk_index"),
                }
                if clip.get("copy_reason"):
                    extra["review_reason"] = str(clip["copy_reason"])[:500]
                hook_end = _hook_end(clip.get("hook_end"), end - start)
                if hook_end is not None:
                    extra["hook_end"] = hook_end
                reasoning = str(clip.get("reason") or "grok")
                if status == "needs_review" and clip.get("copy_reason"):
                    # Motive first so a long model reason cannot push it off the 500-char cut.
                    reasoning = f"{clip['copy_reason']} | {reasoning}"
                reasoning = reasoning[:500]
                c = Candidate(
                    campaign_id=asset.campaign_id,
                    asset_id=asset.id,
                    start_time=round(start, 2),
                    end_time=round(end, 2),
                    score=float(clip.get("score") or 0),
                    reasoning=reasoning,
                    extra_metadata=extra,
                    status=status,
                )
                db.add(c)
                db.flush()
                created.append(c)
                print(f"candidate {c.id} {start}-{end} status={c.status} score={c.score}")
            db.commit()
            # needs_review stays for a person. --approve only takes a clip whose copy passed.
            ready = next((c for c in created if c.status == "pending"), None)
            if args.approve and ready is not None:
                print(approve_candidate(db, ready.id))
            done += 1
            if done >= args.limit:
                break
        print(f"grok_clip_decider_tick done={done}")
        return 0
    except Exception as e:
        db.rollback()
        print(f"ERROR {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
