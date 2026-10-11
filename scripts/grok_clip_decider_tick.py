#!/usr/bin/env python3
"""Speech clip-decider. One Grok JSON call per transcribed asset without candidates.

The caption is written against the campaign CopyRule and checked with
verifier.check_copy. A failure is retried once with those failures in the
prompt. A second failure stores the candidate as needs_review. The score
comes from clip_score (duration fit, blended with the model score)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path("/opt/clipping-system")
sys.path.insert(0, str(ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

PROMPT = """Pick 1 or 2 clip windows from this transcript for short-form video.
Rules: duration_min={dmin}, duration_max={dmax} seconds. Format 9:16.
Campaign rules (must be respected):
{brief}
Copy rules for the title and the caption (must be respected):
{copy_rules}
Return JSON:
{{"clips":[{{"start":0.0,"end":20.0,"reason":"why this window","score":0.82,"title":"max 80 chars hook","caption":"the full post caption that satisfies the copy rules above","on_screen_text":"short overlay text or empty","hook_end":1.5}}]}}
score is your confidence this window will perform, from 0 to 1. Base it on the window. Do not reuse one number for every clip.
hook_end is optional: seconds from the clip start where the hook ends. Omit the key when you are not sure. Do not guess.
Prefer complete phrases. Do not exceed duration_max. Stay inside video_duration={duration}.
Transcript segments (start,end,text):
"""


def _fmt(value: str) -> str:
    """Keep campaign text from breaking str.format braces."""
    return value.replace("{", "{{").replace("}", "}}")


def build_decider_prompt(*, dmin, dmax, duration, brief: str, copy_rules: str) -> str:
    return PROMPT.format(
        dmin=dmin,
        dmax=dmax,
        duration=duration,
        brief=_fmt(brief),
        copy_rules=_fmt(copy_rules),
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


def prepare_clips(clips, dmin: float, dmax: float) -> list[dict]:
    """Keep the windows the decider will store. Same bounds as before."""
    prepared = []
    for clip in list(clips or [])[:2]:
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


def segment_lines(segs, limit: int = 80) -> list[str]:
    """Format transcript segments the way the live decider prompt expects."""
    lines: list[str] = []
    for s in (segs or [])[:limit]:
        if not isinstance(s, dict):
            continue
        lines.append(f"{s.get('start',0):.1f}-{s.get('end',0):.1f} {(s.get('text') or '').strip()}")
    return lines


def build_prompt(*, dmin: float, dmax: float, duration: float, brief: str, segments) -> str:
    """Pure prompt builder. Slice applies to the segment block only (live behavior)."""
    brief = brief or "- (none beyond duration/format)"
    return PROMPT.format(dmin=dmin, dmax=dmax, duration=duration, brief=brief) + "\n".join(segment_lines(segments))[:5000]


def parse_clips(data, dmin: float, dmax: float) -> list[dict]:
    """Pure parse of a Grok JSON payload into candidate fields. No DB writes."""
    clips = data.get("clips") if isinstance(data, dict) else None
    if not clips:
        return []
    out: list[dict] = []
    for clip in clips[:2]:
        start = float(clip.get("start") or 0)
        end = float(clip.get("end") or 0)
        if end - start < max(3.0, dmin * 0.5):
            continue
        if end - start > dmax + 1:
            end = start + dmax
        title = str(clip.get("title") or "")[:100]
        caption = str(clip.get("caption") or clip.get("reason") or "")[:800]
        extra = {
            "source": "grok_clip_decider",
            "kind": "speech",
            "title": title,
            "caption": caption,
            "on_screen_text": str(clip.get("on_screen_text") or "")[:120],
        }
        hook_end = _hook_end(clip.get("hook_end"), end - start)
        if hook_end is not None:
            extra["hook_end"] = hook_end
        out.append({
            "start": round(start, 2),
            "end": round(end, 2),
            "score": 0.7,
            "reason": str(clip.get("reason") or "grok")[:500],
            "extra": extra,
        })
    return out


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
    from app.services.grok_client import grok_chat_json
    from app.services.publish_copy import clip_score, copy_rules_for_prompt, review_clip_copies
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
            lines = segment_lines(segs)
            duration = float(asset.duration_seconds or 0) or dmax
            brief = decider_brief(campaign) or "- (none beyond duration/format)"
            rs = ruleset(campaign)
            copy_rules = copy_rules_for_prompt(rs)
            prompt = build_decider_prompt(
                dmin=dmin, dmax=dmax, duration=duration, brief=brief, copy_rules=copy_rules,
            ) + "\n".join(lines)[:5000]
            print(f"asset={asset.id} campaign={asset.campaign_id} calling grok segs={len(lines)}")
            if args.dry_run:
                done += 1
                if done >= args.limit:
                    break
                continue

            def _clips_of(data) -> list[dict]:
                raw = data.get("clips") if isinstance(data, dict) else None
                return prepare_clips(raw, dmin, dmax)

            data = grok_chat_json(prompt, stage="clip_decider", campaign_id=asset.campaign_id, asset_id=asset.id)
            clips = _clips_of(data)
            if not clips:
                print("no clips", data)
                continue

            def _retry(failures: str) -> list[dict]:
                print(f"asset={asset.id} copy retry")
                again = grok_chat_json(
                    retry_prompt(prompt, failures),
                    stage="clip_decider",
                    campaign_id=asset.campaign_id,
                    asset_id=asset.id,
                )
                return _clips_of(again)

            reviewed = review_clip_copies(clips, rs, retry=_retry)
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
                    score=clip_score(end - start, dmin, dmax, clip.get("score")),
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
