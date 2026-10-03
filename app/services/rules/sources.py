"""Gather the full rule text of a campaign (#33).

Google Docs are exported as HTML (not txt) so hyperlinks survive as
"text (url)"; nothing is truncated here (the extractor chunks).
"""
from __future__ import annotations

import html
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

from app.services.brief_materials import collect_reference_urls, google_doc_id

_UA = "clipping-system-vps/1.0 (+rules-reader; low volume)"
_BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol"}


def _unwrap_google_redirect(href: str) -> str:
    try:
        u = urlparse(href)
    except ValueError:
        return href
    if u.netloc.endswith("google.com") and u.path == "/url":
        q = parse_qs(u.query).get("q")
        if q:
            return q[0]
    return href


class _DocHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._href: list[str | None] = []
        self._skip = 0
        self._link_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script", "head", "title"):
            self._skip += 1
            return
        if tag == "a":
            self._href.append(_unwrap_google_redirect(dict(attrs).get("href") or ""))
            self._link_text = []
        elif tag in _BLOCK:
            self.out.append("\n")
        if tag == "li":
            self.out.append("• ")

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head", "title"):
            self._skip = max(0, self._skip - 1)
            return
        if tag == "a" and self._href:
            href = self._href.pop()
            text = "".join(self._link_text).strip()
            if href and href.startswith("http") and href not in text:
                self.out.append(f" ({href})")
        elif tag in _BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if self._skip:
            return
        self.out.append(data)
        if self._href:
            self._link_text.append(data)


def html_to_text(raw: str) -> str:
    p = _DocHTML()
    p.feed(raw)
    text = html.unescape("".join(p.out)).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def fetch_google_doc(url: str, *, timeout_s: float = 20.0, opener=None) -> tuple[str | None, str | None]:
    doc_id = google_doc_id(url)
    if not doc_id:
        return None, "not_a_google_doc"
    export = f"https://docs.google.com/document/d/{doc_id}/export?format=html"
    req = urllib.request.Request(export, headers={"User-Agent": _UA})
    try:
        op = opener or urllib.request.urlopen
        with op(req, timeout=timeout_s) as r:
            raw = r.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return None, f"{type(e).__name__}: {e}"
    if "accounts.google.com" in raw[:5000] or "<title>Google Docs</title>" in raw[:3000] and "ServiceLogin" in raw:
        return None, "doc_not_public"
    return html_to_text(raw), None


@dataclass
class SourceBundle:
    name: str = ""
    description: str = ""
    guidelines: str = ""
    creator_description: str = ""
    requirement: dict = field(default_factory=dict)
    structured_rules: dict = field(default_factory=dict)
    platforms: list[str] = field(default_factory=list)
    content_types: list[str] = field(default_factory=list)
    reference_urls: list[str] = field(default_factory=list)
    docs: list[dict] = field(default_factory=list)  # {url, ok, error, chars, text}

    def sections(self) -> list[tuple[str, str]]:
        """(source_kind, text) in priority order — docs are the source of truth."""
        out: list[tuple[str, str]] = []
        if self.guidelines:
            out.append(("guidelines", self.guidelines))
        if self.creator_description:
            out.append(("guidelines", re.sub(r"<[^>]+>", " ", self.creator_description)))
        if self.description:
            out.append(("description", self.description))
        for d in self.docs:
            if d.get("text"):
                out.append(("doc", d["text"]))
        return out

    def full_text(self) -> str:
        return "\n\n".join(t for _, t in self.sections())


def _strip_tags(s: str | None) -> str:
    return re.sub(r"<[^>]+>", " ", s or "").strip()


def gather_sources(campaign, *, fetch_doc=fetch_google_doc) -> SourceBundle:
    meta = campaign.source_metadata or {}
    disc = meta.get("discovered") or {}
    detail = meta.get("detail") or {}
    econ = meta.get("economics") or {}
    b = SourceBundle(
        name=campaign.name or "",
        description=disc.get("description") or "",
        guidelines=detail.get("guidelines") or "",
        creator_description=_strip_tags(detail.get("creator_description")),
        requirement=detail.get("requirement") or {},
        structured_rules=detail.get("rules") or {},
        platforms=list(econ.get("platforms") or disc.get("platforms") or []),
        content_types=list(detail.get("content_types") or []),
    )
    refs = collect_reference_urls(disc)
    for rm in detail.get("reference_material") or []:
        u = rm.get("url") if isinstance(rm, dict) else rm
        if isinstance(u, str) and u not in refs:
            refs.append(u)
    b.reference_urls = refs
    seen: set[str] = set()
    for u in refs + re.findall(r"https?://docs\.google\.com/document/d/[\w-]+[^\s)\"']*", b.guidelines):
        did = google_doc_id(u)
        if not did or did in seen:
            continue
        seen.add(did)
        text, err = fetch_doc(u)
        b.docs.append({"url": u, "ok": text is not None, "error": err, "chars": len(text or ""), "text": text or ""})
    return b
