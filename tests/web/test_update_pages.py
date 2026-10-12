import random
import threading

import httpx
import pytest
from fastapi.testclient import TestClient

from morning_edition import db, pipeline, updates
from morning_edition.config import AppConfig
from morning_edition.pipeline import Services
from morning_edition.web.app import create_app
from .conftest import FakePrinter
from .test_settings_pages import FORM

URL = "http://watchtower:8080"
TOKEN = "tok-s3cret-value"
IMAGE = "ghcr.io/rickgram/morning-edition"
COMMIT = {"sha": "0123456789abcdef0123456789abcdef01234567",
          "commit": {"message": "feat: <b>one-click</b> updates\n\nbody", "committer": {"date": "2026-10-11T22:15:09Z"}}}


def make(tmp_path, **cfg):
    config = AppConfig(data_dir=tmp_path, dashboard_password="hunter2", session_secret="test-secret", **cfg)
    services = Services(http=httpx.Client(), llm_factory=lambda s: None, printer=FakePrinter(),
                        rng=random.Random(1))
    app = create_app(config, services, start_scheduler=False)
    client = TestClient(app)
    client.post("/login", data={"password": "hunter2"})
    return app, client


@pytest.fixture
def wired(tmp_path):
    return make(tmp_path, watchtower_url=URL, watchtower_token=TOKEN, app_version="v1.2.0")


def containers(update_available):
    return {"containers": [{"name": "morning-edition-1", "image": IMAGE + ":latest",
                            "update_available": update_available, "current_image_version": "v1.2.0"}],
            "count": 1, "timestamp": "t", "api_version": "v1"}


def test_update_routes_require_login(client):
    for path in ("/updates/check", "/updates/apply"):
        r = client.post(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login"


def test_section_without_a_token_explains_the_updater_and_the_manual_command(tmp_path):
    _, client = make(tmp_path)
    page = client.get("/settings").text
    assert '<h2 id="updates">Updates</h2>' in page and "development build" in page
    assert "docker compose pull &amp;&amp; docker compose up -d" in page and "README" in page
    assert 'action="/updates/check"' not in page and 'action="/updates/apply"' not in page
    assert 'name="auto_update"' not in page


def test_section_with_a_token_shows_version_buttons_and_the_overnight_option(wired):
    _, client = wired
    page = client.get("/settings").text
    assert '<h2 id="updates">Updates</h2>' in page and "v1.2.0" in page and "development build" not in page
    assert 'action="/updates/check"' in page and 'action="/updates/apply"' in page
    assert 'name="auto_update"' in page and 'name="update_time"' in page and 'value="02:30"' in page
    assert TOKEN not in page and "Not checked yet" in page


def test_a_dev_version_label_reads_as_a_development_build(tmp_path):
    _, client = make(tmp_path, watchtower_url=URL, watchtower_token=TOKEN, app_version="dev")
    assert "development build" in client.get("/settings").text


def test_check_for_updates_records_and_shows_an_available_update(wired, network):
    app, client = wired
    check = network.post(f"{URL}/v1/check").mock(return_value=httpx.Response(200, json=containers(True)))
    network.get("https://api.github.com/repos/rickgram/morning-edition/commits/main").mock(
        return_value=httpx.Response(200, json=COMMIT))
    r = client.post("/updates/check", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/settings#updates"
    assert check.calls.last.request.headers["authorization"] == f"Bearer {TOKEN}"
    page = client.get("/settings").text
    assert "Update available" in page and "0123456" in page and "2026-10-11" in page
    assert "feat: &lt;b&gt;one-click&lt;/b&gt; updates" in page and "<b>one-click</b>" not in page
    assert 'href="https://github.com/rickgram/morning-edition/commits/main"' in page
    assert TOKEN not in page


def test_check_for_updates_shows_up_to_date_and_errors(wired, network):
    app, client = wired
    network.post(f"{URL}/v1/check").mock(return_value=httpx.Response(200, json=containers(False)))
    network.get(host="api.github.com").mock(return_value=httpx.Response(403))
    client.post("/updates/check")
    assert "Up to date" in client.get("/settings").text
    network.post(f"{URL}/v1/check").mock(return_value=httpx.Response(401))
    client.post("/updates/check")
    page = client.get("/settings").text
    assert "refused the token" in page and "Up to date" not in page and TOKEN not in page


def test_check_without_a_token_makes_no_request_and_says_why(tmp_path, network):
    _, client = make(tmp_path)
    route = network.route()
    r = client.post("/updates/check")
    assert r.status_code == 200 and "updater" in r.text and not route.called


def test_apply_starts_the_update_and_shows_the_restart_page(wired, network):
    app, client = wired
    update = network.post(f"{URL}/v1/update").mock(side_effect=httpx.ReadError("reset"))
    r = client.post("/updates/apply")
    app.state.last_thread.join(timeout=5)
    assert r.status_code == 200
    assert "Updating. The dashboard restarts in about a minute." in r.text
    assert '<meta http-equiv="refresh" content="45;url=/">' in r.text
    req = update.calls.last.request
    assert req.headers["authorization"] == f"Bearer {TOKEN}" and dict(req.url.params) == {"image": IMAGE}
    assert updates.load_status(app.state.config.data_dir).update_result == "started"


def test_apply_is_refused_while_an_edition_is_building(wired, network):
    app, client = wired
    route = network.route()
    assert pipeline.BUILD_LOCK.acquire(blocking=False)
    try:
        r = client.post("/updates/apply")
    finally:
        pipeline.BUILD_LOCK.release()
    assert r.status_code == 200 and "building" in r.text and "refresh" not in r.text
    assert not route.called and app.state.last_thread is None


def test_apply_without_a_token_makes_no_request(tmp_path, network):
    app, client = make(tmp_path)
    route = network.route()
    r = client.post("/updates/apply")
    assert r.status_code == 200 and "refresh" not in r.text and not route.called


def test_a_failed_update_request_is_shown_on_the_settings_page(wired, network):
    app, client = wired
    network.post(f"{URL}/v1/update").mock(side_effect=httpx.ConnectError("refused"))
    client.post("/updates/apply")
    app.state.last_thread.join(timeout=5)
    page = client.get("/settings").text
    assert "could not be reached" in page and TOKEN not in page


def test_overnight_setting_saves_with_the_main_form(wired):
    app, client = wired
    r = client.post("/settings", data={**FORM, "auto_update": "on", "update_time": "03:15"})
    assert "Saved" in r.text
    s = db.load_settings(db.connect(app.state.db_path))
    assert s.auto_update is True and s.update_time == "03:15"
    client.post("/settings", data={**FORM, "update_time": "03:15"})
    assert db.load_settings(db.connect(app.state.db_path)).auto_update is False


def test_a_bad_overnight_time_is_rejected(wired):
    _, client = wired
    assert "update_time" in client.post("/settings", data={**FORM, "update_time": "9pm"}).text


def test_saving_a_form_without_the_overnight_fields_keeps_the_saved_values(tmp_path):
    app, client = make(tmp_path)   # no updater, so the form has no overnight fields
    conn = db.connect(app.state.db_path)
    db.save_settings(conn, db.load_settings(conn).model_copy(update={"auto_update": True, "update_time": "04:00"}))
    client.post("/settings", data=FORM)
    s = db.load_settings(db.connect(app.state.db_path))
    assert s.auto_update is True and s.update_time == "04:00"
