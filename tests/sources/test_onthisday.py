from datetime import date

import httpx
import respx

from morning_edition.sources import onthisday

URL = "https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/selected/10/12"
DATA = {"selected": [
    {"year": 2013, "text": "A building collapsed (pictured) in Medellin.", "pages": []},
    {"year": 1492, "text": "Columbus reached the Bahamas.", "pages": []},
    {"year": 1800, "text": "A treaty was signed.", "pages": []},
    {"year": 1901, "text": "The White House got its name (depicted).", "pages": []},
    {"year": 1960, "text": "A shoe was banged at the UN.", "pages": []},
]}


@respx.mock
def test_picks_spread_of_years_and_strips_picture_notes():
    respx.get(URL).mock(return_value=httpx.Response(200, json=DATA))
    with httpx.Client() as client:
        items = onthisday.fetch_on_this_day(client, date(2026, 10, 12))
    assert [i.year for i in items] == [1492, 1901, 2013]
    assert items[2].text == "A building collapsed in Medellin."
    assert items[1].text == "The White House got its name."


@respx.mock
def test_returns_empty_on_error():
    respx.get(URL).mock(return_value=httpx.Response(503))
    with httpx.Client() as client:
        assert onthisday.fetch_on_this_day(client, date(2026, 10, 12)) == []


@respx.mock
def test_handles_malformed_entries():
    malformed = {"selected": [
        {"year": "abc", "text": "Bad year"},
        {"year": 2013, "text": "Good entry"},
    ]}
    respx.get(URL).mock(return_value=httpx.Response(200, json=malformed))
    with httpx.Client() as client:
        assert onthisday.fetch_on_this_day(client, date(2026, 10, 12)) == []


@respx.mock
def test_count_one_returns_one():
    respx.get(URL).mock(return_value=httpx.Response(200, json=DATA))
    with httpx.Client() as client:
        items = onthisday.fetch_on_this_day(client, date(2026, 10, 12), count=1)
    assert len(items) == 1
    assert items[0].year == 1492
