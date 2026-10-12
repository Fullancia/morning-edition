from datetime import datetime, timedelta, timezone

import httpx
import respx

from morning_edition.db import Feed
from morning_edition.sources import feeds

NOW = datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc)

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Example Local</title>
<item><title>Seawall vote passes</title><link>http://www.example.com/seawall/?utm_source=rss&amp;id=7</link>
<description>&lt;p&gt;The council voted &lt;b&gt;5-4&lt;/b&gt;.&lt;/p&gt;</description>
<pubDate>Mon, 12 Oct 2026 02:00:00 GMT</pubDate></item>
<item><title>Old news</title><link>https://example.com/old</link>
<pubDate>Fri, 09 Oct 2026 02:00:00 GMT</pubDate></item>
<item><title>No date story</title><link>https://example.com/nodate</link></item>
</channel></rss>"""


def feed(id=1, url="https://example.com/feed", section="Local", headlines_only=False):
    return Feed(id=id, url=url, section=section, enabled=True, headlines_only=headlines_only)


def test_normalize_url_strips_tracking_and_www():
    assert feeds.normalize_url("http://www.Example.com/a/?utm_source=x&id=7#frag") == "https://example.com/a?id=7"
    assert feeds.normalize_url("https://example.com/") == "https://example.com/"


def test_parse_feed_window_and_undated():
    items = feeds.parse_feed(RSS, feed(), NOW, timedelta(hours=24))
    titles = [i.title for i in items]
    assert titles == ["Seawall vote passes", "No date story"]  # old dropped, undated kept
    assert items[0].summary == "The council voted 5-4 ."
    assert items[0].source == "Example Local"
    assert items[0].key == "https://example.com/seawall?id=7"


@respx.mock
def test_gather_dedupes_against_history_and_across_feeds():
    respx.get("https://example.com/feed").mock(return_value=httpx.Response(200, content=RSS))
    respx.get("https://other.com/feed").mock(return_value=httpx.Response(200, content=RSS))
    with httpx.Client() as client:
        result = feeds.gather(
            client, [feed(1), feed(2, "https://other.com/feed", "World")], NOW,
            seen={"https://example.com/nodate"},
        )
    assert [i.title for i in result.items] == ["Seawall vote passes"]
    assert result.items[0].id == 1
    assert result.errors == []


@respx.mock
def test_gather_survives_dead_and_non_feed_sources():
    respx.get("https://dead.com/feed").mock(side_effect=httpx.ConnectTimeout("timed out"))
    respx.get("https://missing.com/feed").mock(return_value=httpx.Response(404))
    respx.get("https://html.com/feed").mock(
        return_value=httpx.Response(200, content=b"<html><body>Not a feed</body></html>"))
    respx.get("https://example.com/feed").mock(return_value=httpx.Response(200, content=RSS))
    sources = [feed(1, "https://dead.com/feed"), feed(2, "https://missing.com/feed"),
               feed(3, "https://html.com/feed"), feed(4)]
    with httpx.Client() as client:
        result = feeds.gather(client, sources, NOW, seen=set())
    assert len(result.errors) == 3
    assert {i.title for i in result.items} == {"Seawall vote passes", "No date story"}


def test_google_news_helpers():
    url = feeds.google_news_url("New York, NY")
    assert url.startswith("https://news.google.com/rss/search?q=New+York%2C+NY")
    assert feeds.is_google_news(url)
    assert not feeds.is_google_news("https://news.example.com/feed")


@respx.mock
def test_gather_survives_an_invalid_url_and_unexpected_errors():
    respx.get("https://example.com/feed").mock(return_value=httpx.Response(200, content=RSS))
    respx.get("https://weird.com/feed").mock(side_effect=KeyError("surprise"))
    with httpx.Client() as client:
        result = feeds.gather(
            client, [feed(1, "http://example.com:abc/feed"), feed(2, "https://weird.com/feed"), feed(3)], NOW, seen=set())
    assert [i.title for i in result.items] == ["Seawall vote passes", "No date story"]
    assert len(result.errors) == 2 and any("KeyError" in e for e in result.errors)
    assert any(e.startswith("http://example.com:abc/feed") for e in result.errors)  # httpx.InvalidURL


@respx.mock
def test_gather_retries_a_flaky_feed():
    route = respx.get("https://example.com/feed").mock(
        side_effect=[httpx.Response(503), httpx.Response(200, content=RSS)])
    with httpx.Client() as client:
        result = feeds.gather(client, [feed()], NOW, seen=set())
    assert route.call_count == 2 and result.errors == [] and len(result.items) == 2


WORDPRESS = (
    "Registration is now open for the 2026 Community Fair [...] "
    "The post Register Now for Community Fair 2026 appeared first on Example Local News."
)


def wordpress_feed(*descriptions):
    entries = "".join(
        f"<item><title>Story {n}</title><link>https://example.com/s{n}</link>"
        f"<description>{d}</description></item>" for n, d in enumerate(descriptions))
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>Example Local News</title>{entries}</channel></rss>'.encode()


def test_strip_boilerplate_real_example_local_news_shape():
    assert feeds.strip_boilerplate(WORDPRESS) == "Registration is now open for the 2026 Community Fair"
    unicode_marker = WORDPRESS.replace("[...]", "[\u2026]")
    assert feeds.strip_boilerplate(unicode_marker) == "Registration is now open for the 2026 Community Fair"


def test_strip_boilerplate_variants_and_keeps_everything_else():
    assert feeds.strip_boilerplate("The council met. THE POST Seawall appeared first on Example Gazette.") == "The council met."
    assert feeds.strip_boilerplate("The council met [...]") == "The council met"
    assert feeds.strip_boilerplate("The council met. Continue reading...") == "The council met."
    assert feeds.strip_boilerplate("The council met. Read more \u2192") == "The council met."
    assert feeds.strip_boilerplate("The council met. read more") == "The council met."
    # a mid-text "The post" and a mid-text marker are real content and stay
    text = "The post office will close. The council met [...] and voted."
    assert feeds.strip_boilerplate(text) == text
    # a legitimate earlier "The post" sentence survives the trailing credit
    both = "The post office will close Friday. The post Office Closing appeared first on Example Gazette."
    assert feeds.strip_boilerplate(both) == "The post office will close Friday."


def test_parse_feed_strips_boilerplate_from_summaries():
    items = feeds.parse_feed(wordpress_feed(f"&lt;p&gt;{WORDPRESS}&lt;/p&gt;"), feed(), NOW, timedelta(hours=24))
    assert items[0].summary == "Registration is now open for the 2026 Community Fair"


def test_boilerplate_only_summary_is_empty_and_teaser_falls_back_to_title():
    from morning_edition.llm import editor
    raw = "[...] The post Only Boilerplate appeared first on Example Local News."
    item = feeds.parse_feed(wordpress_feed(raw), feed(), NOW, timedelta(hours=24))[0]
    assert item.summary == ""
    piece = editor.condense(None, item, None, "story")
    assert piece.paragraphs == [item.title]


def test_strip_boilerplate_only_acts_at_sentence_starts():
    prose = "Critics said the post about the dredging appeared first on Facebook."
    assert feeds.strip_boilerplate(prose) == prose
    prose = "Voters who want to read more"
    assert feeds.strip_boilerplate(prose) == prose
    prose = "Officials asked residents to continue reading..."
    assert feeds.strip_boilerplate(prose) == prose
    prose = "Critics said. the post about it appeared first on Facebook and spread widely across town."
    assert feeds.strip_boilerplate(prose) != prose  # a real sentence start still counts
    for marker in ("[...]", "[\u2026]"):
        text = f"Registration is open {marker} The post Register Now appeared first on Example Local News."
        assert feeds.strip_boilerplate(text) == "Registration is open"
    assert feeds.strip_boilerplate("The post Only appeared first on Example Local News.") == ""
    assert feeds.strip_boilerplate("Registration is open. Read more...") == "Registration is open."
    assert feeds.strip_boilerplate("Registration is open [\u2026] Continue reading") == "Registration is open"
