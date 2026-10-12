"""Find feeds for a reader: Claude researches publications, this module checks every candidate itself."""
import logging
import math
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Literal
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, create_model, field_validator

from ..db import Feed
from ..llm.client import LLM
from ..settings import GOOD_NEWS
from . import feeds as feeds_mod
from .articles import fetch_article
from .http import BLOCKED_MESSAGE, MAX_BODY_BYTES, get_with_retry

log = logging.getLogger("morning_edition")

RECENT = timedelta(days=7)
MAX_CANDIDATES = 25
MAX_WORKERS = 8
MAX_PROMPT_FEEDS = 100
CANDIDATE_BUDGET = 45.0  # seconds a candidate gets, per wave of MAX_WORKERS, before it is given up on
# why a candidate did not make the list
NO_FEED = "no_feed"            # reachable, but no working feed turned up
STALE = "stale"                # a working feed with nothing from the last week
SUBSCRIBED = "subscribed"      # the reader already has it
BLOCKED = "blocked"            # pointed at a local or otherwise non-public address
UNREACHABLE = "unreachable"    # the site could not be fetched (down, timed out, too big) or checking it failed
OVER_BUDGET = "over_budget"    # the check ran past its time budget
DUPLICATE = "duplicate"        # a different outlet with the same feed
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


def resolve_section(raw: str, valid: list[str]) -> str:
    """`raw` matched to a valid section ignoring case and padding. Anything else (an interest like
    "Formula 1" used as a section name) goes to the first configured section after the local one,
    so the outlet is kept rather than dropped."""
    by_name = {v.lower(): v for v in valid}
    exact = by_name.get(str(raw).strip().lower())
    if exact is not None:
        return exact
    configured = [v for v in valid if v != GOOD_NEWS]
    if len(configured) > 1:
        return configured[1]
    return configured[0] if configured else GOOD_NEWS


def candidate_list_model(valid: list[str]) -> type[CandidateList]:
    """CandidateList for one run: `section` is an enum of exactly `valid`, so structured output
    cannot return anything else. A stray value (the model is a guest in its own schema) is repaired
    by resolve_section instead of failing the whole search."""
    names = tuple(dict.fromkeys(valid))
    item = create_model(
        "Candidate", __base__=Candidate,
        __validators__={"repair_section": field_validator("section", mode="before")(
            lambda cls, v: resolve_section(v, list(names)))},
        section=(Literal[names], ...))
    return create_model("CandidateList", __base__=CandidateList, candidates=(list[item], ...))


class DiscoverySummary(BaseModel):
    """What one search did, so an empty result can be explained."""
    proposed: int = 0           # candidates Claude returned
    checked: int = 0            # after dropping bad addresses and repeat hosts, and the cap
    verified: int = 0
    rejected: dict[str, int] = {}
    notes_excerpt: str = ""     # the start of Claude's research text, kept only when it proposed nothing


class DiscoveryResult(BaseModel):
    feeds: list["DiscoveredFeed"] = []
    summary: DiscoverySummary = DiscoverySummary()


class DiscoveredFeed(BaseModel):
    name: str
    feed_url: str
    homepage: str
    section: str
    reason: str
    recent_count: int
    sample_titles: list[str]
    headlines_only: bool


DiscoveryResult.model_rebuild()


RESEARCH_SYSTEM = """You help one reader fill a personal printed newspaper by finding publications \
whose RSS feeds they can subscribe to. Use web search to find real publications (outlets, not \
individual articles) that publish free, full-text articles and have an RSS or Atom feed.

Rules:
- Cover every section the reader lists. The first section is their local section: its outlets must \
cover the reader's home area (the town, county, or nearby region).
- Also cover a "Good News" section: outlets that publish uplifting news about people, communities, \
nature, and progress.
- For each interest the reader lists (AI, space, a sport, a hobby), find outlets for it and assign \
them to the closest of the reader's sections. Interests are topics, never section names: do not \
invent a section for one.
- Skip news aggregators (Google News, Yahoo News, MSN), outlets behind a paywall or a login, and \
every feed the reader already has.
- Find 3 to 6 outlets per section.
- For each outlet give: its name, its homepage URL, its RSS or Atom feed URL if you found one \
(otherwise say none), the section it belongs to (exactly one of the reader's sections or "Good News"), \
and one short reason it suits this reader.
Only report outlets you found by searching. Never invent a URL."""

EXTRACT_SYSTEM = """You turn research notes about news publications into a structured list. One \
candidate per publication, using only what the notes say. Use null for feed_url when the notes do not \
give a feed URL; never guess one. Keep each reason to one short sentence. The section must be exactly \
one of the allowed sections; when the notes file an outlet under an interest or a topic that is not \
an allowed section, pick the closest allowed section."""


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


class _Trace:
    """What went wrong while looking for one candidate's feed, for the reason it gets turned down."""

    def __init__(self) -> None:
        self.blocked = False      # a fetch was refused because the address is not public
        self.unreachable = False  # the homepage could not be fetched at all


def _get(client: httpx.Client, url: str, trace: _Trace | None = None) -> httpx.Response | None:
    """The response, or None when the site can't be reached at all (or sent more than MAX_BODY_BYTES)."""
    try:
        return get_with_retry(client, url, headers={"User-Agent": feeds_mod.USER_AGENT}, timeout=15,
                              follow_redirects=True, max_bytes=MAX_BODY_BYTES)
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        if trace is not None and BLOCKED_MESSAGE in str(exc):
            trace.blocked = True
        return None


def _feed_bytes(client: httpx.Client, url: str, trace: _Trace | None = None) -> bytes | None:
    """The body at `url` when it parses as a feed, else None."""
    resp = _get(client, url, trace)
    if resp is None or resp.status_code >= 400:
        return None
    probe = Feed(id=0, url=url, section="", enabled=True, headlines_only=False)
    try:
        feeds_mod.parse_feed(resp.content, probe, datetime.now(timezone.utc), timedelta(days=36500))
    except ValueError:
        return None
    return resp.content


def _find(client: httpx.Client, candidate: Candidate, trace: _Trace | None = None) -> tuple[str, bytes] | None:
    """The first feed URL that really parses as a feed, with its body."""
    trace = trace or _Trace()
    if candidate.feed_url and _is_web_url(candidate.feed_url):
        body = _feed_bytes(client, candidate.feed_url, trace)
        if body is not None:
            return candidate.feed_url, body
    if not _is_web_url(candidate.homepage):
        return None
    home = _get(client, candidate.homepage, trace)
    if home is None:
        trace.unreachable = True
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
        body = _feed_bytes(client, url, trace)
        if body is not None:
            return url, body
    return None


def find_feed_url(client: httpx.Client, candidate: Candidate) -> str | None:
    found = _find(client, candidate)
    return found[0] if found else None


def verify(client: httpx.Client, candidate: Candidate, now: datetime,
           existing_keys: set[str]) -> tuple[DiscoveredFeed | None, str | None]:
    """(feed, None) for a candidate with a working feed the reader doesn't have and news from the last
    week; otherwise (None, why) with why one of the reason constants. Never raises."""
    try:
        if candidate.feed_url and feeds_mod.normalize_url(candidate.feed_url) in existing_keys:
            return None, SUBSCRIBED
        trace = _Trace()
        found = _find(client, candidate, trace)
        if found is None:
            return None, BLOCKED if trace.blocked else UNREACHABLE if trace.unreachable else NO_FEED
        feed_url, body = found
        if feeds_mod.normalize_url(feed_url) in existing_keys:
            return None, SUBSCRIBED
        probe = Feed(id=0, url=feed_url, section=candidate.section, enabled=True, headlines_only=False)
        items = [i for i in feeds_mod.parse_feed(body, probe, now, RECENT) if i.published is not None]
        if not items:
            return None, STALE
        items.sort(key=lambda i: i.published, reverse=True)
        headlines_only = feeds_mod.is_google_news(feed_url) or fetch_article(client, items[0].url) is None
        return DiscoveredFeed(
            name=candidate.name.strip(), feed_url=feed_url, homepage=candidate.homepage.strip(),
            section=candidate.section, reason=candidate.reason.strip(), recent_count=len(items),
            sample_titles=[i.title for i in items[:3]], headlines_only=headlines_only), None
    except Exception as exc:  # noqa: BLE001 - one odd site must never sink the whole search
        log.info("Feed discovery skipped %s: %s", urlsplit(candidate.homepage).hostname, type(exc).__name__)
        return None, UNREACHABLE


def _prompt_url(url: str) -> str:
    """scheme://host/path only: no query string or login details go to Claude."""
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme, parts.hostname or "", parts.path, "", ""))


def _host(url: str) -> str:
    return urlsplit(url.strip()).netloc.lower().removeprefix("www.")


def discover(llm: LLM, client: httpx.Client, interests: str, home: str, sections: list[str],
             existing_feeds: list[Feed], now: datetime) -> DiscoveryResult:
    """Raises LLMError when Claude can't research or structure the answer."""
    valid = [*sections, GOOD_NEWS]
    user = "\n".join([
        f"Home area: {home or '(not set)'}",
        f"Interests (topics to cover, not section names): {interests.strip() or '(none listed)'}",
        f"Sections: {', '.join(sections)}, and {GOOD_NEWS}",
        "Feeds the reader already has (skip these):",
        *[f"- {_prompt_url(f.url)}" for f in existing_feeds[:MAX_PROMPT_FEEDS]],
    ])
    notes = llm.research(RESEARCH_SYSTEM, user)
    extracted = llm.ask_json(
        EXTRACT_SYSTEM,
        f"Sections allowed: {', '.join(valid)}\n\nResearch notes:\n{notes}", candidate_list_model(valid))

    seen: set[str] = set()
    candidates: list[Candidate] = []
    for c in extracted.candidates:
        host = _host(c.homepage)
        if not _is_web_url(c.homepage) or host in seen:
            continue
        seen.add(host)
        section = resolve_section(c.section, valid)
        if section != c.section:
            log.info("Feed discovery moved %s from section %r to %r", urlsplit(c.homepage).hostname, c.section, section)
        candidates.append(c.model_copy(update={"section": section}))
    candidates = candidates[:MAX_CANDIDATES]

    existing_keys = {feeds_mod.normalize_url(f.url) for f in existing_feeds}
    found, rejected = _verify_all(client, candidates, now, existing_keys)
    order = {s: i for i, s in enumerate(valid)}
    found.sort(key=lambda v: (order[v.section], -v.recent_count))
    unique: dict[str, DiscoveredFeed] = {}
    for v in found:
        if feeds_mod.normalize_url(v.feed_url) in unique:
            rejected[DUPLICATE] += 1  # two outlets can share one feed
        else:
            unique[feeds_mod.normalize_url(v.feed_url)] = v
    summary = DiscoverySummary(
        proposed=len(extracted.candidates), checked=len(candidates), verified=len(unique),
        rejected=dict(rejected), notes_excerpt="" if extracted.candidates else notes.strip()[:300])
    log.info("Feed discovery finished: proposed=%d checked=%d verified=%d rejected=%s", summary.proposed,
             summary.checked, summary.verified,
             " ".join(f"{k}:{v}" for k, v in sorted(summary.rejected.items())) or "none")
    return DiscoveryResult(feeds=list(unique.values()), summary=summary)


def _verify_all(client: httpx.Client, candidates: list[Candidate], now: datetime,
                existing_keys: set[str]) -> tuple[list[DiscoveredFeed], Counter]:
    """Verify concurrently; a host that stalls past its time budget is dropped instead of stalling the run.
    Returns the verified feeds and a count of rejections by reason."""
    pool = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    rejected: Counter = Counter()
    try:
        futures = [(c, pool.submit(verify, client, c, now, existing_keys)) for c in candidates]
        deadline = time.monotonic() + CANDIDATE_BUDGET * math.ceil(len(candidates) / MAX_WORKERS)
        out: list[DiscoveredFeed] = []
        for candidate, future in futures:
            try:
                result, why = future.result(timeout=max(0.0, deadline - time.monotonic()))
            except FutureTimeout:
                result, why = None, OVER_BUDGET
            if result is not None:
                out.append(result)
            else:
                why = why or UNREACHABLE
                rejected[why] += 1
                log.info("Feed discovery rejected %s: %s", urlsplit(candidate.homepage).hostname, why)
        return out, rejected
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
