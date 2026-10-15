"""Prompts for the shadow graph's propose_clips node."""

SYSTEM = "Return only valid JSON. No markdown."

USER = """Pick up to {n} clip windows from this transcript chunk for short-form video.
Rules: duration_min={dmin}, duration_max={dmax} seconds. Format 9:16.
Stay inside video_duration={duration}. Prefer complete phrases.
This is chunk {chunk}/{chunks} covering {chunk_start:.1f}-{chunk_end:.1f}s. Only pick windows inside this chunk.

Campaign rules (must be respected):
{brief}

Return JSON matching:
{{"clips":[{{"start":0.0,"end":20.0,"title":"max 80 chars hook","caption":"2-3 lines, no prices, no [whop]","on_screen_text":"short overlay or empty","hook_end":1.5,"keywords":["transcript word"],"emojis":[{{"emoji":"🔥","keyword":"transcript word"}}],"reason":"why this window","hook":0.82,"flow":0.70,"value":0.75,"fit":0.80}}]}}
hook, flow, value and fit are 0-1 sub-scores for THIS window. Score each clip on its own. Do not reuse one number.
hook_end is optional: seconds from the clip start where the hook ends. Omit the key when unsure. Do not guess.
keywords: 0 to 3 words copied from the transcript. emojis: 0 to 2 objects {{"emoji","keyword"}} tied to those words. Empty lists when nothing fits. Do not put emoji characters in on_screen_text.

{similar}

Transcript segments (start,end,text) for this chunk only:
{segments}
"""


def build_propose_prompt(
    *,
    dmin: float,
    dmax: float,
    duration: float,
    brief: str,
    segment_block: str,
    similar_examples: list[dict] | None = None,
    n: int = 3,
    chunk: int = 1,
    chunks: int = 1,
    chunk_start: float = 0.0,
    chunk_end: float = 0.0,
) -> str:
    from app.services.decider import MAX_SEGMENT_BLOCK_CHARS

    similar = ""
    if similar_examples:
        lines = []
        for ex in similar_examples[:8]:
            lines.append(
                f"- {ex.get('start')}-{ex.get('end')} title={ex.get('title')!r} "
                f"score={ex.get('score')} campaign={ex.get('campaign_id')}"
            )
        similar = "Similar campaigns (read-only, for calibration):\n" + "\n".join(lines)
    return USER.format(
        n=int(n),
        dmin=dmin,
        dmax=dmax,
        duration=duration,
        brief=brief or "- (none beyond duration/format)",
        similar=similar,
        chunk=int(chunk),
        chunks=int(chunks),
        chunk_start=float(chunk_start),
        chunk_end=float(chunk_end) if chunk_end else float(duration or 0),
        segments=segment_block[:MAX_SEGMENT_BLOCK_CHARS],
    )
