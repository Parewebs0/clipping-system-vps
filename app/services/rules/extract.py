"""LLM extraction of the RuleSet + coverage second pass (#33).

Flow: structured mapping (no LLM) → first pass per chunk (LLM, JSON) → merge →
evidence verification → coverage: every normative source line without
evidence goes to a second LLM pass that maps it (field / account / manual /
unsupported / not a rule). Enforcement is assigned afterwards (enforcement.py).
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Callable

from app.services.rules.schema import (
    AccountRequirement,
    Evidence,
    ManualCheck,
    RuleSet,
    UnsupportedRule,
)
from app.services.rules.sources import SourceBundle
from app.services.rules.structured import apply_structured

CHUNK_CHARS = 24000
LLMFn = Callable[..., dict]

FIRST_PASS_PROMPT = """You extract the COMPLETE rule set of a short-form clipping campaign.
Read every line of the sources. The brief documents are the source of truth.
Return ONE JSON object with exactly these keys (use null / [] / false when the
source says nothing; never invent). "evidence" = list of LITERAL quotes copied
character-for-character from the sources (short, the sentence that states the rule).
Optional / recommended items: keep required=false and still quote them.

{
 "platforms": ["youtube", ...],
 "duration": {"required": bool, "min_s": number|null, "max_s": number|null, "evidence": []},
 "aspect": {"required": bool, "ratio": "9:16"|"1:1"|"16:9"|null, "evidence": []},
 "language": {"required": bool, "code": "ISO-639-1"|null, "evidence": []},
 "captions": {"required": bool (burned-in subtitles / captions on the video / text so it works on mute), "brand_dictionary": [exact brand spellings captions must use], "evidence": []},
 "on_screen_text": {"required": bool, "must_include": [literal words the text MUST contain; never copy examples], "guidance": str|null, "evidence": []},
 "logo": {"required": bool, "url": str|null (direct link to the logo file), "position": "top_left"|"top_right"|"bottom_left"|"bottom_right"|"center"|null, "timing": "full"|"start"|"end", "min_seconds": number|null, "as_cta": bool, "cta_text": str|null, "evidence": []},
 "audio": {"required": bool, "original_only": bool, "no_added_music": bool, "must_use_provided_sound": bool (only a platform sound/song the post must use, NOT the footage's own audio), "music_recommended": bool, "evidence": []},
 "hook": {"required": bool, "max_seconds": number|null, "evidence": []},
 "edit": {"required": bool (no raw rips / must be edited / no unedited reuploads), "allowed_edits": [], "evidence": []},
 "source": {"required": bool, "only_provided": bool, "allowed_urls": [urls of official footage], "evidence": []},
 "copy": {"exact_caption": str|null (only if a word-for-word caption is mandated), "must_mention_any": [brand words the post caption must mention], "mentions_by_platform": {"youtube": ["@x"], "tiktok": [], "instagram": [], "x": []}, "pinned_comment": str|null, "evidence": [],
          "hashtags": {"required": bool, "ordered": ["#a", "#b"], "position": "after_text"|"anywhere", "max_extra": int|null, "evidence": []},
          "ftc": {"required": bool, "tokens": ["#Ad", ...], "own_line": bool, "first_after_text": bool, "evidence": []}},
 "prohibitions": {"terms": [short literal words/names (1-3 words) that must not appear in the clip or its text], "topics": [semantic prohibitions], "evidence": []},
 "account_requirements": [{"kind": "bio"|"link_in_bio"|"audience"|"engagement"|"warmup"|"posting_limit"|"keep_live"|"community"|"likes_public"|"dedicated_account"|"other", "text": str, "evidence": []}],
 "manual_checks": [{"text": "content/quality rule a reviewer must check on the clip", "evidence": []}],
 "pre_approval": {"required": bool, "evidence": []},
 "unsupported": [{"text": str, "reason": str, "evidence": []}],
 "content_source_urls": [urls where the footage to clip lives]
}
Platform structured flags (logo, link in bio, face on camera, reposts, pre-approval…) are handled
separately: only extract what the TEXT says.
"unsupported": ONLY rules the source explicitly states that REQUIRE something inside the video an automated
editor cannot produce; each MUST quote the source. Never list things the source does not ask for.
Prohibitions (things NOT to do: no X, don't Y, avoid Z, not allowed…) are NEVER "unsupported": put them in
prohibitions (terms/topics) — not doing something is always possible.
"account_requirements": ONLY obligations (not permissions such as "you do not need…").
Account-level rules (bio, audience %, engagement, warmup, posts/day, keep live N days, join Discord, likes visible) go to account_requirements.
SOURCES:
"""

SECOND_PASS_PROMPT = """These lines from a clipping campaign brief are not yet covered by the extracted rules.
For EACH line decide one action:
  "field"       — it is a rule that belongs to an existing field (give "field" among: duration, aspect, language, captions, on_screen_text, logo, audio, hook, edit, source, copy, copy.hashtags, copy.ftc, prohibitions) and optional "add" (list of literal terms to append: for prohibitions → terms, on_screen_text → must_include, copy → must_mention_any, captions → brand_dictionary);
  "account"     — account-level requirement (give "kind");
  "manual"      — content/quality rule a reviewer must check on each clip;
  "unsupported" — it REQUIRES something INSIDE THE VIDEO that an automated editor cannot produce (give "reason"); prohibitions ("no X", "don't", "avoid", "not allowed") are NEVER unsupported (use "manual" or "field": prohibitions); posting behaviour, caption styling and platform settings are "account" or "ignore";
  "ignore"      — not a rule (headings, payout info, links lists, examples, optional tips).
Return JSON: {"items": [{"i": <line number>, "action": "...", "field": str|null, "add": [], "kind": str|null, "reason": str|null}]}
Current rules (compact): %s
LINES:
%s
"""

_NORMATIVE = re.compile(
    r"\b(must|required?|requirement|mandatory|need(s)? to|have to|do not|don't|dont|never|not allowed|"
    r"avoid|only|at least|minimum|maximum|exact|tag|hashtag|disclos|caption|subtitle|watermark|logo|"
    r"music|audio|sound|language|english|seconds|reject|banned|ban|prohibit|forbidden|no\s+\w+)\b|[✔❌⚠✅🚫]",
    re.I,
)


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = re.sub(r"[“”\"'‘’`*_•·]", "", s)
    s = re.sub(r"[^\w#@:/.\-% ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _tokens(s: str) -> set[str]:
    return {t for t in norm(s).split() if len(t) > 2}


def quote_in(quote: str, text_norm: str) -> bool:
    q = norm(quote)
    if not q:
        return False
    if q in text_norm:
        return True
    qt = _tokens(quote)
    if len(qt) < 3:
        return False
    tt = set(text_norm.split())
    return len(qt & tt) / len(qt) >= 0.85


def normative_lines(text: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in (text or "").splitlines():
        line = raw.strip(" \t•-*")
        if not (8 <= len(line) <= 500):
            continue
        if re.fullmatch(r"\(?https?://\S+\)?", line):
            continue
        if not _NORMATIVE.search(line):
            continue
        k = norm(line)
        if k and k not in seen:
            seen.add(k)
            out.append(line)
    return out


def chunk_sections(sections: list[tuple[str, str]], size: int = CHUNK_CHARS) -> list[str]:
    blocks: list[str] = []
    for kind, text in sections:
        paras = re.split(r"\n\s*\n", text)
        cur = f"[{kind}]\n"
        for p in paras:
            if len(cur) + len(p) > size and len(cur) > 20:
                blocks.append(cur)
                cur = f"[{kind} cont.]\n"
            cur += p + "\n\n"
        blocks.append(cur)
    chunks: list[str] = []
    cur = ""
    for b in blocks:
        if cur and len(cur) + len(b) > size:
            chunks.append(cur)
            cur = ""
        cur += b
    if cur:
        chunks.append(cur)
    return chunks or [""]


def _merge(a: Any, b: Any) -> Any:
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = _merge(out.get(k), v) if k in out else v
        return out
    if isinstance(a, list) and isinstance(b, list):
        out = list(a)
        keys = {json.dumps(x, sort_keys=True, ensure_ascii=False) for x in out}
        for x in b:
            k = json.dumps(x, sort_keys=True, ensure_ascii=False)
            if k not in keys:
                keys.add(k)
                out.append(x)
        return out
    if isinstance(a, bool) and isinstance(b, bool):
        return a or b
    if a in (None, "", [], {}):
        return b
    return a


def _ev_list(raw, sections_norm: list[tuple[str, str]]) -> list[Evidence]:
    out: list[Evidence] = []
    for q in raw or []:
        if isinstance(q, dict):
            q = q.get("quote") or ""
        q = str(q).strip()
        if not q:
            continue
        src = "llm"
        ok = False
        for kind, tn in sections_norm:
            if quote_in(q, tn):
                src, ok = kind, True
                break
        out.append(Evidence(quote=q[:500], source=src, verified=ok))
    return out


_RULE_KEYS = ("duration", "aspect", "language", "captions", "on_screen_text", "logo", "audio",
              "hook", "edit", "source", "prohibitions", "pre_approval")


def llm_to_ruleset(data: dict, bundle: SourceBundle) -> tuple[RuleSet, list[str]]:
    secs = [(k, norm(t)) for k, t in bundle.sections()]
    errors: list[str] = []
    rs = RuleSet()
    d = dict(data or {})

    def fix(section: dict | None) -> dict:
        section = dict(section or {})
        section["evidence"] = [e.model_dump() for e in _ev_list(section.get("evidence"), secs)]
        return {k: v for k, v in section.items() if v is not None}

    for key in _RULE_KEYS:
        if isinstance(d.get(key), dict):
            try:
                setattr(rs, key, type(getattr(rs, key)).model_validate(fix(d[key])))
            except Exception as e:  # noqa: BLE001
                errors.append(f"{key}: {e}"[:200])
    cp = d.get("copy") if isinstance(d.get("copy"), dict) else {}
    try:
        c = fix(cp)
        c["hashtags"] = fix(cp.get("hashtags"))
        c["ftc"] = fix(cp.get("ftc"))
        mbp = c.get("mentions_by_platform") or {}
        c["mentions_by_platform"] = {str(k).lower(): [str(x) for x in (v or [])] for k, v in mbp.items() if v}
        rs.copy_rules = type(rs.copy_rules).model_validate(c)
    except Exception as e:  # noqa: BLE001
        errors.append(f"copy: {e}"[:200])
    for item in d.get("account_requirements") or []:
        if isinstance(item, dict) and item.get("text"):
            rs.account_requirements.append(AccountRequirement(
                required=True, kind=str(item.get("kind") or "other"), text=str(item["text"])[:400],
                evidence=_ev_list(item.get("evidence"), secs)))
    for item in d.get("manual_checks") or []:
        if isinstance(item, dict) and item.get("text"):
            rs.manual_checks.append(ManualCheck(required=True, text=str(item["text"])[:400],
                                                evidence=_ev_list(item.get("evidence"), secs)))
    for item in d.get("unsupported") or []:
        if isinstance(item, dict) and item.get("text"):
            rs.unsupported.append(UnsupportedRule(text=str(item["text"])[:400], reason=str(item.get("reason") or "")[:300],
                                                  evidence=_ev_list(item.get("evidence"), secs)))
    rs.platforms = [str(p).lower() for p in d.get("platforms") or [] if p]
    rs.content_source_urls = [u for u in d.get("content_source_urls") or [] if isinstance(u, str) and u.startswith("http")]
    return rs, errors


def sanitize(rs: RuleSet) -> RuleSet:
    """Deterministic clean-up of LLM output."""
    # Unsupported items must be grounded in the source.
    rs.unsupported = [u for u in rs.unsupported if any(e.verified for e in u.evidence)]
    rs.account_requirements = [a for a in rs.account_requirements
                               if not re.search(r"\b(do not need|don't need|not required|optional)\b", a.text, re.I)]
    # A hashtag list made only of FTC tokens is the disclosure, not a hashtag rule.
    ftc_l = {t.lower() for t in rs.copy_rules.ftc.tokens}
    ordered = rs.copy_rules.hashtags.ordered
    if ordered and ftc_l and all(h.lower() in ftc_l for h in ordered):
        rs.copy_rules.hashtags.ordered = []
    # Long "terms" are really topics.
    terms, topics = [], list(rs.prohibitions.topics)
    for t in rs.prohibitions.terms:
        (terms if len(t.split()) <= 3 else topics).append(t)
    seen: set[str] = set()
    rs.prohibitions.terms = [t for t in terms if not (t.lower() in seen or seen.add(t.lower()))]
    seen = set()
    rs.prohibitions.topics = [t for t in topics if not (t.lower() in seen or seen.add(t.lower()))]
    # on-screen must_include: drop long phrases (examples), keep literal words.
    rs.on_screen_text.must_include = [m for m in rs.on_screen_text.must_include if len(m.split()) <= 4]
    return rs


_NEGATIVE = re.compile(
    r"^\W*(no|not|don'?t|do not|never|avoid|without|stop|zero|using|adding|uploading|posting|reposting|"
    r"uso de|usar|sin|nada de|prohibid\w*|forbidden|prohibited|banned)\b", re.I)
_NEG_CONTEXT = re.compile(r"\b(not allowed|prohibited|forbidden|banned|don'?t|do not|never|avoid|no permitid\w*|prohibid\w*)\b", re.I)
_INFORMATIVE = re.compile(r"\b(not expected|not required|optional|no need|you do not need|you don'?t need|no required)\b", re.I)
_COPY_KIND = re.compile(r"tag|mention|caption|hashtag|ftc|disclosure", re.I)


def reclassify(rs: RuleSet) -> RuleSet:
    """#39: deterministic guard on LLM classification.

    * An LLM "unsupported" item phrased as a prohibition is not unsupported
      (not doing something is always possible) → manual check per clip.
    * Purely informative lines ("you are not expected to…") are dropped.
    * Account items that are really copy rules (tagging, caption, hashtags)
      go to the copy (already covered) or to manual checks.
    Deterministic unsupported items (host, mandatory sound) are added later
    by enforcement.py and are not affected.
    """
    keep: list[UnsupportedRule] = []
    for u in rs.unsupported:
        quote = u.evidence[0].quote if u.evidence else ""
        if _INFORMATIVE.search(u.text) or _INFORMATIVE.search(quote):
            continue
        if _NEGATIVE.search(u.text) or _NEGATIVE.search(quote) or _NEG_CONTEXT.search(u.reason or "") \
                or _NEG_CONTEXT.search(quote):
            rs.manual_checks.append(ManualCheck(required=True, text=f"Prohibido: {u.text}"[:400], evidence=u.evidence))
            continue
        keep.append(u)
    rs.unsupported = keep
    cp = rs.copy_rules
    has_mentions = bool(cp.must_mention_any or any(cp.mentions_by_platform.values()))
    accounts: list[AccountRequirement] = []
    for a in rs.account_requirements:
        if _INFORMATIVE.search(a.text):
            continue
        if _COPY_KIND.search(a.kind or "") and not (a.kind or "").startswith("audio"):
            if not has_mentions:
                rs.manual_checks.append(ManualCheck(required=True, text=f"Copy: {a.text}"[:400], evidence=a.evidence))
            else:
                cp.evidence += a.evidence
            continue
        accounts.append(a)
    rs.account_requirements = accounts
    return rs


def all_evidence(rs: RuleSet) -> list[Evidence]:
    evs: list[Evidence] = []
    for key in _RULE_KEYS:
        evs += getattr(rs, key).evidence
    evs += rs.copy_rules.evidence + rs.copy_rules.hashtags.evidence + rs.copy_rules.ftc.evidence
    for lst in (rs.account_requirements, rs.manual_checks):
        for r in lst:
            evs += r.evidence
    for u in rs.unsupported:
        evs += u.evidence
    return evs


def _covered(line: str, quotes_norm: list[str], extra_norm: list[str]) -> bool:
    ln = norm(line)
    lt = _tokens(line)
    for q in quotes_norm + extra_norm:
        if not q:
            continue
        if q in ln or (len(ln) > 15 and ln in q):
            return True
        qt = set(t for t in q.split() if len(t) > 2)
        if lt and qt and (len(lt & qt) / max(1, min(len(lt), len(qt)))) >= 0.7:
            return True
    return False


def _compact(rs: RuleSet) -> str:
    d = rs.dump()
    for k in list(d):
        if isinstance(d[k], dict):
            d[k] = {kk: vv for kk, vv in d[k].items() if kk not in ("evidence", "enforcement", "scope", "note")}
    d.pop("coverage", None)
    d.pop("meta", None)
    return json.dumps(d, ensure_ascii=False)[:6000]


_ADD_TARGET = {
    "prohibitions": ("prohibitions", "terms"),
    "on_screen_text": ("on_screen_text", "must_include"),
    "copy": ("copy_rules", "must_mention_any"),
    "captions": ("captions", "brand_dictionary"),
}


def second_pass(rs: RuleSet, bundle: SourceBundle, llm: LLMFn, campaign_id: int | None) -> RuleSet:
    lines = normative_lines(bundle.full_text())
    quotes = [norm(e.quote) for e in all_evidence(rs)]
    extra = [norm(x) for x in (rs.copy_rules.exact_caption or "",)]
    uncovered = [ln for ln in lines if not _covered(ln, quotes, extra)]
    cov = rs.coverage
    cov.normative_lines = len(lines)
    cov.covered_first_pass = len(lines) - len(uncovered)
    if not uncovered:
        return rs
    batch = uncovered[:120]
    numbered = "\n".join(f"{i}. {ln}" for i, ln in enumerate(batch))
    data = llm(SECOND_PASS_PROMPT % (_compact(rs), numbered), stage="rules_coverage", campaign_id=campaign_id)
    handled: set[int] = set()
    for it in (data or {}).get("items") or []:
        try:
            i = int(it.get("i"))
            line = batch[i]
        except (TypeError, ValueError, IndexError):
            continue
        handled.add(i)
        ev = [Evidence(quote=line[:500], source="doc", verified=True)]
        action = str(it.get("action") or "").lower()
        if action == "field":
            fld = str(it.get("field") or "")
            if fld in _ADD_TARGET and it.get("add"):
                attr, lst = _ADD_TARGET[fld]
                obj = getattr(rs, attr)
                cur = getattr(obj, lst)
                for x in it["add"]:
                    if isinstance(x, str) and x and x not in cur:
                        cur.append(x)
            obj = None
            if fld == "copy":
                obj = rs.copy_rules
            elif fld == "copy.hashtags":
                obj = rs.copy_rules.hashtags
            elif fld == "copy.ftc":
                obj = rs.copy_rules.ftc
            elif fld in _RULE_KEYS:
                obj = getattr(rs, fld)
            if obj is not None:
                obj.evidence += ev
                cov.mapped_second_pass += 1
            else:
                rs.manual_checks.append(ManualCheck(required=True, text=line[:400], evidence=ev))
                cov.mapped_second_pass += 1
        elif action == "account":
            rs.account_requirements.append(AccountRequirement(required=True, kind=str(it.get("kind") or "other"),
                                                              text=line[:400], evidence=ev))
            cov.mapped_second_pass += 1
        elif action == "manual":
            rs.manual_checks.append(ManualCheck(required=True, text=line[:400], evidence=ev))
            cov.mapped_second_pass += 1
        elif action == "unsupported":
            rs.unsupported.append(UnsupportedRule(text=line[:400], reason=str(it.get("reason") or "")[:300], evidence=ev))
            cov.unsupported_second_pass += 1
        else:
            cov.ignored += 1
    cov.uncovered = [batch[i] for i in range(len(batch)) if i not in handled] + uncovered[120:]
    return rs


def extract_ruleset(bundle: SourceBundle, llm: LLMFn, *, campaign_id: int | None = None) -> RuleSet:
    merged: dict = {}
    chunks = chunk_sections(bundle.sections())
    header = json.dumps({
        "name": bundle.name, "platforms": bundle.platforms, "content_types": bundle.content_types,
        "reference_urls": bundle.reference_urls[:40],
    }, ensure_ascii=False)
    for i, ch in enumerate(chunks):
        prompt = FIRST_PASS_PROMPT + header + f"\n[chunk {i + 1}/{len(chunks)}]\n" + ch
        data = llm(prompt, stage="rules_reader", campaign_id=campaign_id)
        merged = _merge(merged, data if isinstance(data, dict) else {})
    rs, errors = llm_to_ruleset(merged, bundle)
    rs = sanitize(rs)
    rs = apply_structured(rs, bundle.requirement, bundle.structured_rules)
    rs = second_pass(rs, bundle, llm, campaign_id)
    rs = reclassify(rs)
    evs = all_evidence(rs)
    rs.coverage.evidence_total = len(evs)
    rs.coverage.evidence_verified = sum(1 for e in evs if e.verified)
    rs.meta.update({
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "chunks": len(chunks),
        "source_chars": len(bundle.full_text()),
        "docs": [{k: v for k, v in d.items() if k != "text"} for d in bundle.docs],
        "validation_errors": errors,
    })
    return rs
