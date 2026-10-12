from datetime import UTC, datetime

import httpx
import pytest
import respx

from morning_edition import updates
from morning_edition.config import AppConfig
from morning_edition.settings import Settings

URL = "http://watchtower:8080"
TOKEN = "tok-s3cret-value"
IMAGE = "ghcr.io/rickgram/morning-edition"
COMMIT = {"sha": "0123456789abcdef0123456789abcdef01234567",
          "commit": {"message": "feat: one-click updates\n\nLonger body here.",
                     "committer": {"date": "2026-10-11T22:15:09Z"}}}


def container(**kw):
    return {"name": "morning-edition-1", "image": f"{IMAGE}:latest", "update_available": False,
            "digest": "sha256:aaa", "latest_digest": "sha256:aaa", "current_image_version": "v1.0.0", **kw}


def body(*containers):
    return {"containers": list(containers), "count": len(containers),
            "timestamp": "2026-10-12T06:00:00Z", "api_version": "v1"}


@pytest.fixture
def client():
    with httpx.Client() as c:
        yield c


@pytest.fixture
def cfg(tmp_path):
    return AppConfig(data_dir=tmp_path, dashboard_password="pw", session_secret="s", watchtower_url=URL,
                     watchtower_token=TOKEN, app_version="v1.0.0")


def nightly(client, cfg, **kw):
    s = Settings(timezone="UTC", print_time="05:30", build_lead_minutes=20, auto_update=True, update_time="02:30")
    s = s.model_copy(update=kw.pop("settings", {}))
    return updates.nightly_update(client, cfg, s, build_running=kw.pop("build_running", False),
                                  now=kw.pop("now", datetime(2026, 10, 12, 2, 30, tzinfo=UTC)))


def test_nightly_checks_then_updates_only_when_available(client, cfg):
    with respx.mock(assert_all_called=True) as router:
        check = router.post(f"{URL}/v1/check").mock(return_value=httpx.Response(
            200, json=body(container(update_available=True))))
        update = router.post(f"{URL}/v1/update").mock(return_value=httpx.Response(200, json={}))
        router.get(host="api.github.com").mock(return_value=httpx.Response(200, json=COMMIT))
        note = nightly(client, cfg)
    assert check.call_count == 1 and update.call_count == 1
    assert "requested" in note
    status = updates.load_status(cfg.data_dir)
    assert status.auto_note == note and status.auto_at and status.update_result == "started"


def test_nightly_does_not_update_when_up_to_date(client, cfg):
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{URL}/v1/check").mock(return_value=httpx.Response(200, json=body(container())))
        update = router.post(f"{URL}/v1/update").mock(return_value=httpx.Response(200, json={}))
        router.get(host="api.github.com").mock(return_value=httpx.Response(200, json=COMMIT))
        note = nightly(client, cfg)
    assert not update.called and "up to date" in note.lower()


def test_nightly_does_not_update_when_the_check_fails(client, cfg):
    with respx.mock(assert_all_called=False) as router:
        router.post(f"{URL}/v1/check").mock(return_value=httpx.Response(500))
        update = router.post(f"{URL}/v1/update").mock(return_value=httpx.Response(200, json={}))
        router.get(host="api.github.com").mock(return_value=httpx.Response(200, json=COMMIT))
        note = nightly(client, cfg)
    assert not update.called and "failed" in note.lower()


def test_nightly_skips_while_a_build_is_running(client, cfg):
    with respx.mock(assert_all_called=False) as router:
        route = router.route()
        note = nightly(client, cfg, build_running=True)
    assert not route.called and "skipped" in note.lower() and "building" in note.lower()
    assert updates.load_status(cfg.data_dir).auto_note == note


def test_nightly_skips_when_the_edition_builds_within_the_hour(client, cfg):
    # print 05:30 with a 20 minute lead builds at 05:10; at 04:30 that is 40 minutes away
    with respx.mock(assert_all_called=False) as router:
        route = router.route()
        note = nightly(client, cfg, now=datetime(2026, 10, 12, 4, 30, tzinfo=UTC))
    assert not route.called and "skipped" in note.lower() and "edition" in note.lower()


def test_nightly_skips_without_a_configured_updater(client, tmp_path):
    bare = AppConfig(data_dir=tmp_path, dashboard_password="p", session_secret="s")
    with respx.mock(assert_all_called=False) as router:
        route = router.route()
        note = nightly(client, bare)
    assert not route.called and "skipped" in note.lower() and "not set up" in note.lower()
    assert updates.load_status(tmp_path).auto_note == note


def test_nightly_saves_the_requested_note_before_sending_the_update(client, cfg):
    seen = {}

    def answer(request):
        seen["note"] = updates.load_status(cfg.data_dir).auto_note   # on disk before the app would be killed
        return httpx.Response(200, json={})

    with respx.mock() as router:
        router.post(f"{URL}/v1/check").mock(return_value=httpx.Response(
            200, json=body(container(update_available=True))))
        router.post(f"{URL}/v1/update").mock(side_effect=answer)
        router.get(host="api.github.com").mock(return_value=httpx.Response(200, json=COMMIT))
        nightly(client, cfg)
    assert seen["note"] == "Update requested."


def test_nightly_overwrites_the_note_when_the_request_fails(client, cfg):
    with respx.mock() as router:
        router.post(f"{URL}/v1/check").mock(return_value=httpx.Response(
            200, json=body(container(update_available=True))))
        router.post(f"{URL}/v1/update").mock(side_effect=httpx.ConnectError("refused"))
        router.get(host="api.github.com").mock(return_value=httpx.Response(200, json=COMMIT))
        note = nightly(client, cfg)
    assert note.startswith("The update request failed.")
    assert updates.load_status(cfg.data_dir).auto_note == note
