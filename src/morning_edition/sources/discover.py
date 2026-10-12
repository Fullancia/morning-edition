"""Find feeds for a reader: Claude researches publications, this module checks every candidate itself."""
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx
from pydantic import BaseModel

from ..db import Feed
from ..llm.client import LLM
from ..settings import GOOD_NEWS
from . import feeds as feeds_mod
from .articles import fetch_article
from .http import get_with_retry

log = logging.getLogger("morning_edition")

RECENT = timedelta(days=7)
MAX_CANDIDATES = 25
MAX_WORKERS = 8
MAX_PROMPT_FEEDS = 100
COMMON_PATHS = ("/feed", "/feed/", "/rss", "/rss.xml", "/feed.xml", "/index.xml", "/atom.xml")
FEED_TYPES = ("application/rss+xml", "application/atom+xml")


class Candidate(BaseModel):
    name: str
    homepage: str
    feed_url: str | None
    section: str
    reason: str


class CandidateList(BaseModel):
    candidates: list[Candidate]


class DiscoveredFeed(BaseModel):
    name: str
    feed_url: str
    homepage: str
    section: str
    reason: str
    recent_count: int
    sample_titles: list[str]
    headlines_only: bool


RESEARCH_SYSTEM = """You help one reader fill a personal printed newspaper by finding publications \
whose RSS feeds they can subscribe to. Use web search to find real publications (outlets, not \
individual articles) that publish free, full-text articles and have an RSS or Atom feed.

Rules:
- Cover every section the reader lists. The first section is their local section: its outlets must \
cover the reader's home area (the town, county, or nearby region).
- Also cover a "Good News" section: outlets that publish uplifting news about people, communities, \
nature, and progress.
- For each interest the reader lists, find outlets and assign them to the closest section.
- Skip news aggregators (Google News, Yahoo News, MSN), outlets behind a paywall or a login, and \
every feed the reader already has.
- Find 3 to 6 outlets per section.
- For each outlet give: its name, its homepage URL, its RSS or Atom feed URL if you found one \
(otherwise say none), the section it belongs to (exactly one of the reader's sections or "Good News"), \
and one short reason it suits this reader.
Only report outlets you found by searching. Never invent a URL."""

EXTRACT_SYSTEM = """You turn research notes about news publications into a structured list. One \
candidate per publication, using only what the notes say. Use null for feed_url when the notes do not \
give a feed URL; never guess one. Keep each reason to one short sentence."""


class _FeedLinks(HTMLParser):
    """Collects the href of every <link rel="alternate" type="application/rss+xml|atom+xml">."""

    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != "link":
            return
        a = {k: (v or "") for k, v in attrs}
        if "alternate" in a.get("rel", "").lower().split() and a.get("type", "").lower() in FEED_TYPES and a.get("href"):
            self.hrefs.append(a["href"].strip())


def _is_web_url(url: str) -> bool:
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False
    return parts.scheme.lower() in ("http", "https") and bool(parts.netloc)


def _get(client: httpx.Client, url: str) -> httpx.Response | None:
    """The response, or None when the site can't be reached at all."""
    try:
        return get_with_retry(client, url, headers={"User-Agent": feeds_mod.USER_AGENT}, timeout=15,
                              follow_redirects=True)
    except (httpx.HTTPError, httpx.InvalidURL):
        return None


def _feed_bytes(client: httpx.Client, url: str) -> bytes | None:
    """The body at `url` when it parses as a feed, else None."""
    resp = _get(client, url)
    if resp is None or resp.status_code >= 400:
        return None
    probe = Feed(id=0, url=url, section="", enabled=True, headlines_only=False)
    try:
        feeds_mod.parse_feed(resp.content, probe, datetime.now(timezone.utc), timedelta(days=36500))
    except ValueError:
        return None
    return resp.content


def _find(client: httpx.Client, candidate: Candidate) -> tuple[str, bytes] | None:
    """The first feed URL that really parses as a feed, with its body."""
    if candidate.feed_url and _is_web_url(candidate.feed_url):
        body = _feed_bytes(client, candidate.feed_url)
        if body is not None:
            return candidate.feed_url, body
    if not _is_web_url(candidate.homepage):
        return None
    home = _get(client, candidate.homepage)
    if home is None:
        return None  # the site is unreachable; its common paths would only time out too
    tried: set[str] = set()
    guesses: list[str] = []
    if home.status_code < 400 and "html" in home.headers.get("content-type", "html"):
        links = _FeedLinks()
        try:
            links.feed(home.text)
        except Exception:  # noqa: BLE001 - a malformed page just yields no links
            pass
        guesses += [urljoin(str(home.url), href) for href in links.hrefs]
    origin = urlsplit(candidate.homepage)
    guesses += [f"{origin.scheme}://{origin.netloc}{path}" for path in COMMON_PATHS]
    for url in guesses:
        if url in tried or not _is_web_url(url):
            continue
        tried.add(url)
        body = _feed_bytes(client, url)
        if body is not None:
            return url, body
    return None


def find_feed_url(client: httpx.Client, candidate: Candidate) -> str | None:
    found = _find(client, candidate)
    return found[0] if found else None


def verify(client: httpx.Client, candidate: Candidate, now: datetime,
           existing_keys: set[str]) -> DiscoveredFeed | None:
    """A candidate that has a working feed the reader doesn't have, with news from the last week."""
    try:
        if candidate.feed_url and feeds_mod.normalize_url(candidate.feed_url) in existing_keys:
            return None
        found = _find(client, candidate)
        if found is None:
            return None
        feed_url, body = found
        if feeds_mod.normalize_url(feed_url) in existing_keys:
            return None
        probe = Feed(id=0, url=feed_url, section=candidate.section, enabled=True, headlines_only=False)
        items = [i for i in feeds_mod.parse_feed(body, probe, now, RECENT) if i.published is not None]
        if not items:
            return None
        items.sort(key=lambda i: i.published, reverse=True)
        headlines_only = feeds_mod.is_google_news(feed_url) or fetch_article(client, items[0].url) is None
        return DiscoveredFeed(
            name=candidate.name.strip(), feed_url=feed_url, homepage=candidate.homepage.strip(),
            section=candidate.section, reason=candidate.reason.strip(), recent_count=len(items),
            sample_titles=[i.title for i in items[:3]], headlines_only=headlines_only)
    except Exception as exc:  # noqa: BLE001 - one odd site must never sink the whole search
        log.info("Feed discovery skipped %s: %s", candidate.homepage, type(exc).__name__)
        return None


def _host(url: str) -> str:
    return urlsplit(url.strip()).netloc.lower().removeprefix("www.")


def discover(llm: LLM, client: httpx.Client, interests: str, home: str, sections: list[str],
             existing_feeds: list[Feed], now: datetime) -> list[DiscoveredFeed]:
    """Raises LLMError when Claude can't research or structure the answer."""
    valid = [*sections, GOOD_NEWS]
    user = "\n".join([
        f"Home area: {home or '(not set)'}",
        f"Interests: {interests.strip() or '(none listed)'}",
        f"Sections: {', '.join(sections)}, and {GOOD_NEWS}",
        "Feeds the reader already has (skip these):",
        *[f"- {f.url}" for f in existing_feeds[:MAX_PROMPT_FEEDS]],
    ])
    notes = llm.research(RESEARCH_SYSTEM, user)
    extracted = llm.ask_json(
        EXTRACT_SYSTEM,
        f"Sections allowed: {', '.join(valid)}\n\nResearch notes:\n{notes}", CandidateList)

    by_name = {s.lower(): s for s in valid}
    seen: set[str] = set()
    candidates: list[Candidate] = []
    for c in extracted.candidates:
        section = by_name.get(c.section.strip().lower())
        host = _host(c.homepage)
        if section is None or not _is_web_url(c.homepage) or host in seen:
            continue
        seen.add(host)
        candidates.append(c.model_copy(update={"section": section}))
    candidates = candidates[:MAX_CANDIDATES]

    existing_keys = {feeds_mod.normalize_url(f.url) for f in existing_feeds}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        verified = list(pool.map(lambda c: verify(client, c, now, existing_keys), candidates))
    order = {s: i for i, s in enumerate(valid)}
    found = [v for v in verified if v is not None]
    found.sort(key=lambda v: (order[v.section], -v.recent_count))
    return found
