import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import httpx

from ..db import Feed
from ..models import FeedItem
from ..textutil import clean_text
from .http import get_with_retry

USER_AGENT = "Mozilla/5.0 (compatible; MorningEdition/0.1; self-hosted personal newspaper)"

_TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid", "cmpid", "ref"}


# WordPress feeds tack boilerplate onto the end of every description.
# Each phrase must start the text or a sentence, so mid-sentence prose is never touched.
_SENTENCE_START = r"(?:^|(?<=[.!?\]\u2026]\s))"
_POST_CREDIT = re.compile(
    _SENTENCE_START + r"The post\s(?:(?!The post\s).)+?\sappeared first on\s.{1,80}$", re.I)
_CONTINUATION = re.compile(r"\[(?:\u2026|\.{2,3})\]\s*$")
_READ_MORE = re.compile(
    _SENTENCE_START + r"(?:continue reading|read more)\s*(?:\.{2,3}|\u2026|\u00bb|\u2192|>>)?\s*$", re.I)


def strip_boilerplate(text: str) -> str:
    """Drop trailing 'The post ... appeared first on ...', '[...]' and 'Continue reading' bits."""
    while True:
        out = text.rstrip()
        for pattern in (_POST_CREDIT, _CONTINUATION, _READ_MORE):
            out = pattern.sub("", out).rstrip()
        if out == text:
            return out
        text = out


def _summary(raw: str) -> str:
    return clean_text(strip_boilerplate(clean_text(raw, 10_000)))


@dataclass
class GatherResult:
    items: list[FeedItem]
    errors: list[str]


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    query = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_KEYS
    ]
    path = parts.path.rstrip("/") or "/"
    scheme = "https" if parts.scheme in ("http", "https") else parts.scheme
    return urlunsplit((scheme, host, path, urlencode(query), ""))


def parse_feed(content: bytes, feed: Feed, now: datetime, window: timedelta) -> list[FeedItem]:
    parsed = feedparser.parse(content)
    if not parsed.entries and (parsed.bozo or not parsed.get("version")):
        raise ValueError(f"not a feed ({parsed.get('bozo_exception', 'no entries')})")
    source = clean_text(parsed.feed.get("title", ""), 80) or urlsplit(feed.url).netloc
    items: list[FeedItem] = []
    for entry in parsed.entries:
        link = entry.get("link")
        title = clean_text(entry.get("title", ""), 200)
        if not link or not title:
            continue
        stamp = entry.get("published_parsed") or entry.get("updated_parsed")
        published = datetime(*stamp[:6], tzinfo=timezone.utc) if stamp else None
        if published is not None and now - published > window:
            continue
        items.append(FeedItem(
            id=0, feed_id=feed.id, section=feed.section, title=title, url=link,
            key=normalize_url(link), summary=_summary(entry.get("summary", "")),
            published=published, source=source, headlines_only=feed.headlines_only,
        ))
    return items


def gather(client: httpx.Client, feeds: list[Feed], now: datetime, seen: set[str],
           window: timedelta = timedelta(hours=24)) -> GatherResult:
    items: list[FeedItem] = []
    errors: list[str] = []
    keys: set[str] = set()
    for feed in feeds:
        if not feed.enabled:
            continue
        try:
            resp = get_with_retry(client, feed.url, headers={"User-Agent": USER_AGENT},
                                  timeout=20, follow_redirects=True)
            resp.raise_for_status()
            found = parse_feed(resp.content, feed, now, window)
        except (httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
            errors.append(f"{feed.url}: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001 - one odd feed must never abort the whole step
            errors.append(f"{feed.url}: {type(exc).__name__}")
            continue
        for item in found:
            if item.key in seen or item.key in keys:
                continue
            keys.add(item.key)
            items.append(item.model_copy(update={"id": len(items) + 1}))
    return GatherResult(items, errors)


def is_google_news(url: str) -> bool:
    return urlsplit(url).netloc.lower() == "news.google.com"


def google_news_url(town: str) -> str:
    return "https://news.google.com/rss/search?" + urlencode(
        {"q": town, "hl": "en-US", "gl": "US", "ceid": "US:en"})
