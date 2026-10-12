from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from morning_edition.db import Feed
from morning_edition.llm.client import LLMError
from morning_edition.sources import discover
from morning_edition.sources.discover import Candidate, CandidateList, DiscoveredFeed
from morning_edition.sources.feeds import normalize_url

NOW = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)

BODY = " ".join(f"Sentence number {i} explains the harbor plan in plain words." for i in range(40))
ARTICLE = f"<html><body><article><h1>Harbor</h1><p>{BODY}</p></article></body></html>"
PAYWALL = "<html><body><article><p>Subscribe to keep reading.</p></article></body></html>"


def rss(*items, title="Example Daily"):
    body = "".join(
        f"<item><title>{t}</title><link>{link}</link><pubDate>{when}</pubDate></item>"
        for t, link, when in items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>{title}</title>{body}</channel></rss>'


def stamp(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).strftime("%a, %d %b %Y %H:%M:%S GMT")


FRESH = rss(("Harbor plan approved", "https://example.com/a1", stamp(1)),
            ("Library hours change", "https://example.com/a2", stamp(2)),
            ("Bridge repairs begin", "https://example.com/a3", stamp(3)),
            ("Fourth story", "https://example.com/a4", stamp(4)),
            ("Month old story", "https://example.com/a0", stamp(30)))
STALE = rss(("Ancient news", "https://example.com/old", stamp(20)))


def cand(homepage="https://example.com", feed_url=None, section="Local", name="Example Daily"):
    return Candidate(name=name, homepage=homepage, feed_url=feed_url, section=section, reason="Covers the area.")


@pytest.fixture(autouse=True)
def quick_retries(monkeypatch):
    monkeypatch.setattr("morning_edition.sources.http._sleep", lambda s: None)


@pytest.fixture
def web():
    with respx.mock(assert_all_mocked=False, assert_all_called=False) as router:
        yield router  # anything not mocked answers 200 with an empty body, like a soft 404


@pytest.fixture
def client():
    with httpx.Client() as c:
        yield c


def xml(body):
    return httpx.Response(200, content=body.encode(), headers={"content-type": "application/rss+xml"})


def html(body):
    return httpx.Response(200, text=body, headers={"content-type": "text/html; charset=utf-8"})


def test_explicit_feed_url_is_used(web, client):
    web.get("https://example.com/custom.xml").mock(return_value=xml(FRESH))
    assert discover.find_feed_url(client, cand(feed_url="https://example.com/custom.xml")) \
        == "https://example.com/custom.xml"


def test_homepage_autodiscovery_resolves_relative_hrefs(web, client):
    web.get("https://example.com/").mock(return_value=html(
        '<html><head><link rel="stylesheet" href="/x.css">'
        '<link rel="alternate" type="application/rss+xml" title="Daily" href="/news/rss-latest"></head></html>'))
    web.get("https://example.com/news/rss-latest").mock(return_value=xml(FRESH))
    assert discover.find_feed_url(client, cand()) == "https://example.com/news/rss-latest"


def test_a_wrong_explicit_url_falls_back_to_autodiscovery(web, client):
    web.get("https://example.com/bad.xml").mock(return_value=html("<html>not a feed</html>"))
    web.get("https://example.com/").mock(return_value=html(
        '<link rel="alternate" type="application/atom+xml" href="https://example.com/atom">'))
    web.get("https://example.com/atom").mock(return_value=xml(FRESH))
    assert discover.find_feed_url(client, cand(feed_url="https://example.com/bad.xml")) \
        == "https://example.com/atom"


def test_common_paths_are_tried_in_order_on_the_origin(web, client):
    web.get("https://example.com/").mock(return_value=html("<html>no links</html>"))
    web.get("https://example.com/rss").mock(return_value=html("<html>soft 200 page</html>"))
    web.get("https://example.com/feed.xml").mock(return_value=xml(FRESH))
    assert discover.find_feed_url(client, cand(homepage="https://example.com/news/")) \
        == "https://example.com/feed.xml"


def test_no_feed_anywhere_gives_none_and_a_dead_site_does_not_raise(web, client):
    assert discover.find_feed_url(client, cand()) is None
    web.get("https://dead.example.com/").mock(side_effect=httpx.ConnectError("down"))
    assert discover.find_feed_url(client, cand(homepage="https://dead.example.com")) is None


def verify(client, candidate, existing=frozenset()):
    return discover.verify(client, candidate, NOW, set(existing))


def test_verify_counts_recent_items_and_keeps_three_newest_titles(web, client):
    web.get("https://example.com/feed").mock(return_value=xml(FRESH))
    web.get("https://example.com/a1").mock(return_value=html(ARTICLE))
    found = verify(client, cand())
    assert isinstance(found, DiscoveredFeed)
    assert found.feed_url == "https://example.com/feed" and found.name == "Example Daily"
    assert found.homepage == "https://example.com" and found.section == "Local"
    assert found.recent_count == 4  # the 30 day old item does not count
    assert found.sample_titles == ["Harbor plan approved", "Library hours change", "Bridge repairs begin"]
    assert found.headlines_only is False


def test_verify_rejects_dead_non_feed_and_stale_candidates(web, client):
    web.get("https://stale.example.com/feed").mock(return_value=xml(STALE))
    web.get("https://html.example.com/feed").mock(return_value=html("<html><body>Hello</body></html>"))
    web.get("https://dead.example.com/").mock(side_effect=httpx.ConnectError("down"))
    assert verify(client, cand(homepage="https://stale.example.com")) is None
    assert verify(client, cand(homepage="https://html.example.com")) is None
    assert verify(client, cand(homepage="https://dead.example.com")) is None


def test_undated_items_do_not_count_as_recent(web, client):
    undated = '<rss version="2.0"><channel><title>T</title><item><title>x</title><link>https://example.com/x</link></item></channel></rss>'
    web.get("https://example.com/feed").mock(return_value=xml(undated))
    assert verify(client, cand()) is None


def test_verify_excludes_feeds_the_user_already_has(web, client):
    web.get("https://example.com/feed").mock(return_value=xml(FRESH))
    web.get("https://example.com/a1").mock(return_value=html(ARTICLE))
    assert verify(client, cand(), existing={normalize_url("http://www.example.com/feed/")}) is None
    assert verify(client, cand(feed_url="https://example.com/feed"),
                  existing={normalize_url("https://example.com/feed")}) is None


def test_paywalled_newest_article_means_headlines_only(web, client):
    web.get("https://example.com/feed").mock(return_value=xml(FRESH))
    web.get("https://example.com/a1").mock(return_value=html(PAYWALL))
    assert verify(client, cand()).headlines_only is True


def test_google_news_feeds_are_headlines_only_without_fetching_articles(web, client):
    url = "https://news.google.com/rss/search?q=example"
    feed = rss(("Story one", "https://news.google.com/rss/articles/abc", stamp(1)), title="Google News")
    web.get(url).mock(return_value=xml(feed))
    found = verify(client, cand(homepage="https://news.google.com", feed_url=url))
    assert found.headlines_only is True
    assert [c.request.url.path for c in web.calls] == ["/rss/search"]


def test_verify_never_raises(web, client, monkeypatch):
    web.get("https://example.com/feed").mock(return_value=xml(FRESH))
    monkeypatch.setattr(discover, "fetch_article", lambda *a: 1 / 0)
    assert verify(client, cand()) is None


class FakeLLM:
    def __init__(self, candidates=(), error=None):
        self.candidates, self.error, self.calls = list(candidates), error, []

    def research(self, system, user, **kwargs):
        self.calls.append(("research", system, user))
        if self.error:
            raise self.error
        return "research notes"

    def ask_json(self, system, user, schema, **kwargs):
        self.calls.append(("ask_json", system, user))
        assert schema is CandidateList
        return CandidateList(candidates=self.candidates)


def run(llm, client, existing=(), sections=("Local", "World", "Tech")):
    return discover.discover(llm, client, interests="sailing, jazz", home="Albany, NY",
                             sections=list(sections), existing_feeds=list(existing), now=NOW)


def test_discover_prompts_carry_the_reader_and_the_existing_feeds(web, client):
    llm = FakeLLM()
    existing = [Feed(id=1, url="https://mine.example.org/feed", section="Local", enabled=True, headlines_only=False)]
    assert run(llm, client, existing) == []
    (_, system, user), (_, ask_system, ask_user) = llm.calls
    assert "Albany, NY" in user and "sailing, jazz" in user
    assert "Local, World, Tech" in user and "Good News" in user
    assert "https://mine.example.org/feed" in user
    assert "Google News" in system and "paywall" in system.lower()
    assert "research notes" in ask_user


def test_discover_verifies_filters_dedupes_and_sorts(web, client):
    quiet = rss(("Quiet week", "https://tech.example.com/t1", stamp(6)))
    for host in ("www.local1", "local2", "good"):
        web.get(f"https://{host}.example.com/feed").mock(return_value=xml(FRESH))
    web.get("https://tech.example.com/feed").mock(return_value=xml(quiet))
    candidates = [
        cand("https://tech.example.com", section="Tech", name="Tech"),
        cand("https://good.example.com", section="Good News", name="Good"),
        cand("https://local2.example.com", section="Local", name="Local Two"),
        cand("https://www.local1.example.com", section="Local", name="Local One"),
        cand("https://local1.example.com/other", section="Local", name="Duplicate host"),
        cand("https://nowhere.example.com", section="Sports", name="Bad section"),
        cand("https://gone.example.com", section="World", name="Dead"),
    ]
    found = run(FakeLLM(candidates), client)
    assert [(f.name, f.section) for f in found] == [
        ("Local Two", "Local"), ("Local One", "Local"), ("Tech", "Tech"), ("Good", "Good News")]


def test_discover_sorts_by_recent_count_within_a_section(web, client):
    few = rss(("One", "https://few.example.com/1", stamp(1)))
    web.get("https://few.example.com/feed").mock(return_value=xml(few))
    web.get("https://many.example.com/feed").mock(return_value=xml(FRESH))
    found = run(FakeLLM([cand("https://few.example.com", name="Few"),
                         cand("https://many.example.com", name="Many")]), client)
    assert [f.name for f in found] == ["Many", "Few"]


def test_discover_drops_existing_feeds_and_caps_candidates_at_25(web, client):
    many = [cand(f"https://site{i}.example.com", name=f"Site {i}") for i in range(40)]
    for i in range(40):
        web.get(f"https://site{i}.example.com/feed").mock(return_value=xml(FRESH))
    assert len(run(FakeLLM(many), client)) == 25
    existing = [Feed(id=1, url="https://site0.example.com/feed/", section="Local", enabled=True,
                     headlines_only=False)]
    found = run(FakeLLM(many[:3]), client, existing)
    assert [f.name for f in found] == ["Site 1", "Site 2"]


def test_discover_llm_failure_propagates(web, client):
    with pytest.raises(LLMError):
        run(FakeLLM(error=LLMError("Claude declined (other)")), client)


# --- hardening: local addresses, size cap, time budget, de-duplication ---

@pytest.fixture
def public():
    from morning_edition.sources.http import public_client
    with public_client() as c:
        yield c


def test_candidates_pointing_at_the_local_network_find_nothing(web, public, monkeypatch):
    from morning_edition.sources import http as http_mod
    inside = web.get(host="10.0.0.5").mock(return_value=xml(FRESH))
    loopback = web.get(host="127.0.0.1").mock(return_value=xml(FRESH))
    for homepage in ("http://127.0.0.1", "http://[::1]", "http://10.0.0.5"):
        assert discover.find_feed_url(public, cand(homepage=homepage)) is None
        assert verify(public, cand(homepage=homepage, feed_url=homepage + "/feed")) is None
    monkeypatch.setattr(http_mod, "resolve", lambda host: ["10.0.0.5"])
    assert verify(public, cand(homepage="https://sneaky.example.com")) is None
    web.get("https://example.com/").mock(return_value=httpx.Response(
        302, headers={"location": "http://169.254.169.254/"}))
    monkeypatch.setattr(http_mod, "resolve", lambda host: ["93.184.216.34"])
    assert verify(public, cand()) is None
    assert not inside.called and not loopback.called


def test_public_candidates_still_verify_through_the_public_client(web, public):
    web.get("https://example.com/feed").mock(return_value=xml(FRESH))
    web.get("https://example.com/a1").mock(return_value=html(ARTICLE))
    assert verify(public, cand()).recent_count == 4


def test_an_oversize_feed_or_page_counts_as_not_a_feed(web, client):
    huge = FRESH + " " * 2_100_000
    web.get("https://example.com/feed").mock(return_value=xml(huge))
    assert verify(client, cand(homepage="https://example.com")) is None
    web.get("https://big.example.com/").mock(return_value=html("<p>" + "x" * 2_100_000))
    assert discover.find_feed_url(client, cand(homepage="https://big.example.com")) is None


def test_existing_feed_urls_reach_claude_without_query_or_login(web, client):
    llm = FakeLLM()
    existing = [Feed(id=1, url="https://user:pw@mine.example.org/feed?token=SECRET", section="Local",
                     enabled=True, headlines_only=False)]
    run(llm, client, existing)
    user = llm.calls[0][2]
    assert "https://mine.example.org/feed" in user
    assert "SECRET" not in user and "user:pw" not in user and "token" not in user


def test_outlets_sharing_one_feed_appear_once(web, client):
    web.get("https://shared.example.com/rss").mock(return_value=xml(FRESH))
    found = run(FakeLLM([cand("https://a.example.com", name="A", feed_url="https://shared.example.com/rss"),
                         cand("https://b.example.com", name="B", feed_url="https://shared.example.com/rss")]),
                client)
    assert [f.name for f in found] == ["A"]


def test_a_stalled_candidate_is_dropped_without_stalling_the_run(web, client, monkeypatch):
    import threading
    release = threading.Event()
    real = discover.verify

    def slow(client, candidate, now, existing_keys):
        if "slow" in candidate.homepage:
            release.wait(5)
            return None
        return real(client, candidate, now, existing_keys)

    monkeypatch.setattr(discover, "verify", slow)
    monkeypatch.setattr(discover, "CANDIDATE_BUDGET", 0.3)
    web.get("https://quick.example.com/feed").mock(return_value=xml(FRESH))
    try:
        found = run(FakeLLM([cand("https://slow.example.com", name="Slow"),
                             cand("https://quick.example.com", name="Quick")]), client)
    finally:
        release.set()
    assert [f.name for f in found] == ["Quick"]
