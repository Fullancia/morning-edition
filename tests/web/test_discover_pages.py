import json
import random
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from morning_edition import db
from morning_edition.config import AppConfig
from morning_edition.llm.client import LLMError
from morning_edition.pipeline import Services
from morning_edition.sources import discover as discover_mod
from morning_edition.sources.discover import DiscoveredFeed
from morning_edition.sources.http import PublicOnlyTransport
from morning_edition.web.app import create_app
from .conftest import FakePrinter


def found(name="Example Daily", url="https://example.com/feed", section="Local", headlines_only=False,
          homepage="https://example.com", count=5):
    return DiscoveredFeed(name=name, feed_url=url, homepage=homepage, section=section,
                          reason="Covers the area closely.", recent_count=count,
                          sample_titles=["Harbor plan approved", "Library hours change", "Bridge repairs"],
                          headlines_only=headlines_only)


@pytest.fixture
def keyed(tmp_path):
    """An app whose Claude key is present (the factory returns a stand-in LLM)."""
    config = AppConfig(data_dir=tmp_path, dashboard_password="hunter2", session_secret="test-secret")
    services = Services(http=httpx.Client(), llm_factory=lambda s: object(), printer=FakePrinter(),
                        rng=random.Random(1))
    app = create_app(config, services, start_scheduler=False)
    client = TestClient(app)
    client.post("/login", data={"password": "hunter2"})
    return app, client


def write_state(app, **state):
    base = {"status": "done", "started_at": "2026-10-12T09:00:00+00:00",
            "run_id": "run1", "finished_at": "2026-10-12T09:01:00+00:00", "error": None, "results": []}
    merged = {**base, **state}
    merged["results"] = [r.model_dump() for r in merged["results"]]
    (app.state.config.data_dir / "discovery.json").write_text(json.dumps(merged))


def read_state(app):
    return json.loads((app.state.config.data_dir / "discovery.json").read_text())


def test_all_three_routes_require_login(client):
    for method, path in (("get", "/feeds/discover"), ("post", "/feeds/discover"),
                         ("post", "/feeds/discover/add")):
        r = getattr(client, method)(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login", path


def test_without_an_api_key_the_page_explains_and_has_no_button(authed):
    text = authed.get("/feeds/discover").text
    assert "Finding feeds needs an Anthropic API key (ANTHROPIC_API_KEY in .env)." in text
    assert 'action="/feeds/discover"' not in text


def test_without_an_api_key_posting_starts_nothing(authed, app):
    r = authed.post("/feeds/discover", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/feeds/discover"
    assert app.state.last_thread is None


def test_idle_page_offers_the_button_and_the_cost(keyed):
    app, client = keyed
    text = client.get("/feeds/discover").text
    assert 'method="post" action="/feeds/discover"' in text and "Find feeds for me" in text
    assert "few cents" in text


def test_search_runs_in_the_background_and_shows_progress_then_results(keyed, monkeypatch):
    app, client = keyed
    gate, calls = threading.Event(), []

    def fake_discover(llm, http, interests, home, sections, existing_feeds, now):
        assert http is not app.state.services.http  # found sites get their own public-only client
        assert isinstance(http._transport, PublicOnlyTransport)
        calls.append((interests, home, sections, [f.url for f in existing_feeds]))
        gate.wait(10)
        return [found()]

    monkeypatch.setattr(discover_mod, "discover", fake_discover)
    conn = db.connect(app.state.db_path)
    s = db.load_settings(conn)
    db.save_settings(conn, s.model_copy(update={"interests": "sailing"}))
    db.add_feed(conn, "https://mine.example.org/feed", "World")
    r = client.post("/feeds/discover", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/feeds/discover"
    assert read_state(app)["status"] == "running"
    page = client.get("/feeds/discover").text
    assert "Searching" in page and '<meta http-equiv="refresh" content="5">' in page
    gate.set()
    app.state.last_thread.join(timeout=10)
    assert calls == [("sailing", "", ["Local", "World", "Tech"], ["https://mine.example.org/feed"])]
    state = read_state(app)
    assert state["status"] == "done" and state["error"] is None and state["finished_at"]
    assert state["results"][0]["feed_url"] == "https://example.com/feed"
    done = client.get("/feeds/discover").text
    assert "Example Daily" in done and 'http-equiv="refresh"' not in done


def test_a_second_post_while_running_starts_no_second_thread(keyed, monkeypatch):
    app, client = keyed
    gate, started = threading.Event(), []
    monkeypatch.setattr(discover_mod, "discover", lambda *a, **k: (gate.wait(10), [])[1])
    real = app.state.start_thread
    app.state.start_thread = lambda *a: (started.append(a), real(*a))
    client.post("/feeds/discover")
    client.post("/feeds/discover")
    assert len(started) == 1
    gate.set()
    app.state.last_thread.join(timeout=10)
    client.post("/feeds/discover")  # finished, so searching again is allowed
    app.state.last_thread.join(timeout=10)
    assert len(started) == 2


def test_claude_failure_is_stored_as_a_short_error(keyed, monkeypatch):
    app, client = keyed

    def boom(*a, **k):
        raise LLMError("Claude declined (other)")

    monkeypatch.setattr(discover_mod, "discover", boom)
    client.post("/feeds/discover")
    app.state.last_thread.join(timeout=10)
    state = read_state(app)
    assert state["status"] == "error" and state["error"] == "Claude declined (other)"
    page = client.get("/feeds/discover").text
    assert "Claude declined (other)" in page and 'action="/feeds/discover"' in page


def test_an_unexpected_failure_hides_its_details(keyed, monkeypatch):
    app, client = keyed

    def boom(*a, **k):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(discover_mod, "discover", boom)
    client.post("/feeds/discover")
    app.state.last_thread.join(timeout=10)
    assert "secret detail" not in json.dumps(read_state(app))
    assert read_state(app)["status"] == "error"


def test_a_running_state_nobody_is_working_on_shows_as_interrupted(keyed):
    app, client = keyed
    write_state(app, status="running", finished_at=None)
    page = client.get("/feeds/discover").text
    assert "interrupted" in page.lower() and 'http-equiv="refresh"' not in page


def test_results_render_grouped_with_escaped_text_and_safe_links(keyed):
    app, client = keyed
    write_state(app, results=[
        found("Harbor <script>alert(1)</script>", "https://a.example.com/feed", "Local"),
        found("Paywalled Post", "https://b.example.com/feed", "World", headlines_only=True,
              homepage="javascript:alert(1)", count=2),
        found("Cheerful", "https://c.example.com/feed", "Good News", homepage="https://c.example.com"),
    ])
    text = client.get("/feeds/discover").text
    assert "<script>alert(1)</script>" not in text and "Harbor &lt;script&gt;" in text
    assert 'href="https://c.example.com" target="_blank" rel="noopener noreferrer"' in text
    assert "javascript:" not in text
    assert text.index("Local") < text.index("World") < text.index("Good News")
    assert "5 stories this week" in text and "2 stories this week" in text
    assert text.count("headlines only") == 1
    assert "Harbor plan approved" in text and "Covers the area closely." in text
    boxes = [line for line in text.splitlines() if 'name="pick"' in line]
    assert len(boxes) == 3 and " checked" in boxes[0] and " checked" not in boxes[1] and " checked" in boxes[2]
    assert '<option value="World" selected>' in text and '<option value="Good News" selected>' in text
    assert 'action="/feeds/discover/add"' in text and "Search again" in text


def test_adding_selected_feeds_uses_the_chosen_sections_and_skips_duplicates(keyed):
    app, client = keyed
    write_state(app, results=[
        found("One", "https://one.example.com/feed", "Local"),
        found("Two", "https://two.example.com/feed", "World", headlines_only=True),
        found("Three", "https://three.example.com/feed", "Tech"),
        found("Dup", "https://dup.example.com/feed", "Local"),
    ])
    conn = db.connect(app.state.db_path)
    db.add_feed(conn, "https://www.dup.example.com/feed/", "World")
    r = client.post("/feeds/discover/add", follow_redirects=False, data={
        "run": "run1", "pick": ["0", "1", "3"],
        "section_0": "Tech", "section_1": "Good News", "section_2": "Local", "section_3": "Local"})
    assert r.status_code == 303
    assert r.headers["location"].startswith("/settings") and r.headers["location"].endswith("#feeds")
    rows = {f.url: (f.section, f.headlines_only) for f in db.list_feeds(conn)}
    assert rows == {"https://www.dup.example.com/feed/": ("World", False),
                    "https://one.example.com/feed": ("Tech", False),
                    "https://two.example.com/feed": ("Good News", True)}
    assert "Added 2 feeds." in client.get(r.headers["location"].split("#")[0]).text


def test_adding_ignores_bad_sections_indexes_and_a_replaced_result_set(keyed):
    app, client = keyed
    write_state(app, results=[found("One", "https://one.example.com/feed", "World")])
    conn = db.connect(app.state.db_path)
    client.post("/feeds/discover/add", data={"run": "old-run", "pick": ["0"]})
    assert db.list_feeds(conn) == []
    client.post("/feeds/discover/add", data={"run": "run1",
                                              "pick": ["0", "9", "x"], "section_0": "Sports"})
    (feed,) = db.list_feeds(conn)
    assert feed.section == "World"  # an unknown section falls back to Claude's suggestion
    assert "Added 1 feed." in client.get("/settings?added=1").text


def test_settings_page_links_to_the_finder(authed):
    text = authed.get("/settings").text
    assert 'href="/feeds/discover"' in text and "Find feeds for me" in text


def test_today_links_to_the_finder_only_when_there_are_no_feeds(authed, app):
    assert 'href="/feeds/discover"' in authed.get("/").text
    db.add_feed(db.connect(app.state.db_path), "https://example.com/feed", "Local")
    assert 'href="/feeds/discover"' not in authed.get("/").text


def test_unicode_digits_never_crash_the_added_message_or_the_pick_list(keyed):
    app, client = keyed
    assert client.get("/settings?added=%C2%B2").status_code == 200
    assert client.get("/settings?added=" + "9" * 5000).status_code == 200
    write_state(app, results=[found("One", "https://one.example.com/feed", "World")])
    r = client.post("/feeds/discover/add", data={"run": "run1", "pick": ["\u00b2", "\u0663", "0"]},
                    follow_redirects=False)
    assert r.status_code == 303
    assert [f.url for f in db.list_feeds(db.connect(app.state.db_path))] == ["https://one.example.com/feed"]


def test_each_search_gets_a_random_run_id(keyed, monkeypatch):
    app, client = keyed
    monkeypatch.setattr(discover_mod, "discover", lambda *a, **k: [found()])
    ids = []
    for _ in range(2):
        client.post("/feeds/discover")
        app.state.last_thread.join(timeout=10)
        ids.append(read_state(app)["run_id"])
    assert len(set(ids)) == 2 and all(len(i) == 32 and int(i, 16) >= 0 for i in ids)
    assert f'name="run" value="{ids[1]}"' in client.get("/feeds/discover").text


def test_adding_while_a_new_search_runs_says_so_and_adds_nothing(keyed):
    app, client = keyed
    write_state(app, results=[found("One", "https://one.example.com/feed", "World")])
    app.state.discover_lock.acquire()
    try:
        r = client.post("/feeds/discover/add", data={"run": "run1", "pick": ["0"]}, follow_redirects=False)
        assert r.headers["location"] == "/feeds/discover?busy=1"
        assert "A new search is running; reload when it finishes." in client.get(r.headers["location"]).text
    finally:
        app.state.discover_lock.release()
    assert db.list_feeds(db.connect(app.state.db_path)) == []
