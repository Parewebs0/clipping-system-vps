"""Prompts for the shadow graph's propose_clips node."""

SYSTEM = "Return only valid JSON. No markdown."

USER = """Pick 1 or 2 clip windows from this transcript for short-form video.
Rules: duration_min={dmin}, duration_max={dmax} seconds. Format 9:16.
Stay inside video_duration={duration}. Prefer complete phrases.

Campaign rules (must be respected):
{brief}

Return JSON matching:
{{"clips":[{{"start":0.0,"end":20.0,"title":"max 80 chars hook","caption":"2-3 lines, no prices, no [whop]","on_screen_text":"short overlay or empty","hook_end":1.5,"reason":"why this window"}}]}}
hook_end is optional: seconds from the clip start where the hook ends. Omit the key when unsure. Do not guess.

{similar}

Transcript segments (start,end,text):
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
) -> str:
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
        dmin=dmin,
        dmax=dmax,
        duration=duration,
        brief=brief or "- (none beyond duration/format)",
        similar=similar,
        segments=segment_block[:5000],
    )
