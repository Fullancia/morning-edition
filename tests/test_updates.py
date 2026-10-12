import json
import logging
from datetime import UTC, datetime

import httpx
import pytest
import respx

from morning_edition import updates
from morning_edition.config import AppConfig

URL = "http://watchtower:8080"
TOKEN = "tok-s3cret-value"
IMAGE = "ghcr.io/rickgram/morning-edition"
NOW = datetime(2026, 10, 12, 6, 0, tzinfo=UTC)


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


# ---- check -----------------------------------------------------------------------------------

def test_check_sends_bearer_token_and_exact_query_and_reports_an_available_update(client):
    with respx.mock(assert_all_called=True) as router:
        route = router.post(f"{URL}/v1/check").mock(
            return_value=httpx.Response(200, json=body(container(update_available=True, latest_digest="sha256:bbb"))))
        result = updates.check(client, URL, TOKEN, IMAGE)
    req = route.calls.last.request
    assert req.headers["authorization"] == f"Bearer {TOKEN}"
    assert dict(req.url.params) == {"image": IMAGE, "timeout": "2m"}
    assert result.update_available is True and result.error == "" and result.current_version == "v1.0.0"


def test_check_reports_up_to_date(client):
    with respx.mock() as router:
        router.post(f"{URL}/v1/check").mock(return_value=httpx.Response(200, json=body(container())))
        result = updates.check(client, URL, TOKEN, IMAGE)
    assert result.update_available is False and result.error == ""


def test_check_with_a_trailing_slash_on_the_url(client):
    with respx.mock(assert_all_called=True) as router:
        router.post(f"{URL}/v1/check").mock(return_value=httpx.Response(200, json=body(container())))
        assert updates.check(client, URL + "/", TOKEN, IMAGE).error == ""


def test_check_surfaces_a_per_container_error(client):
    with respx.mock() as router:
        router.post(f"{URL}/v1/check").mock(return_value=httpx.Response(
            200, json=body(container(error="registry unreachable"))))
        result = updates.check(client, URL, TOKEN, IMAGE)
    assert result.update_available is False and "registry unreachable" in result.error


def test_check_never_repeats_the_token_even_if_the_updater_echoes_it(client):
    with respx.mock() as router:
        router.post(f"{URL}/v1/check").mock(return_value=httpx.Response(
            200, json=body(container(error=f"denied for {TOKEN}"))))
        result = updates.check(client, URL, TOKEN, IMAGE)
    assert result.error and TOKEN not in result.error


def test_check_with_no_containers_says_the_updater_cannot_see_the_app(client):
    with respx.mock() as router:
        router.post(f"{URL}/v1/check").mock(return_value=httpx.Response(200, json=body()))
        result = updates.check(client, URL, TOKEN, IMAGE)
    assert result.update_available is False and "label" in result.error


@pytest.mark.parametrize("response", [
    httpx.Response(200, text="not json"),
    httpx.Response(200, json=["a", "list"]),
    httpx.Response(200, json={"containers": "nope"}),
    httpx.Response(200, json={"containers": ["nope"]}),
    httpx.Response(401, text="unauthorized"),
    httpx.Response(500, text="boom"),
])
def test_check_bad_answers_become_an_error_result(client, response):
    with respx.mock() as router:
        router.post(f"{URL}/v1/check").mock(return_value=response)
        result = updates.check(client, URL, TOKEN, IMAGE)
    assert result.update_available is False and result.error
    assert TOKEN not in result.error


def test_check_unreachable_updater_is_an_error_never_a_raise_and_never_echoes_exception_text(client, caplog):
    caplog.set_level(logging.DEBUG)
    with respx.mock() as router:
        router.post(f"{URL}/v1/check").mock(side_effect=httpx.ConnectError(f"cannot connect with {TOKEN}"))
        result = updates.check(client, URL, TOKEN, IMAGE)
    assert result.update_available is False and result.error
    assert TOKEN not in result.error and TOKEN not in caplog.text


# ---- GitHub ----------------------------------------------------------------------------------

COMMIT = {"sha": "0123456789abcdef0123456789abcdef01234567",
          "commit": {"message": "feat: one-click updates\n\nLonger body here.",
                     "committer": {"date": "2026-10-11T22:15:09Z"}}}


def test_latest_release_info_parses_the_newest_commit(client):
    with respx.mock(assert_all_called=True) as router:
        route = router.get("https://api.github.com/repos/rickgram/morning-edition/commits/main").mock(
            return_value=httpx.Response(200, json=COMMIT))
        info = updates.latest_release_info(client, "rickgram/morning-edition")
    assert info == ("0123456", "feat: one-click updates", "2026-10-11")
    assert "authorization" not in route.calls.last.request.headers


@pytest.mark.parametrize("response", [
    httpx.Response(403, json={"message": "API rate limit exceeded"}),
    httpx.Response(404, json={"message": "Not Found"}),
    httpx.Response(200, text="not json"),
    httpx.Response(200, json={"sha": "abc"}),
    httpx.Response(200, json=["x"]),
])
def test_latest_release_info_failures_are_none(client, response):
    with respx.mock() as router:
        router.get("https://api.github.com/repos/rickgram/morning-edition/commits/main").mock(return_value=response)
        assert updates.latest_release_info(client, "rickgram/morning-edition") is None


def test_latest_release_info_network_error_is_none(client):
    with respx.mock() as router:
        router.get(host="api.github.com").mock(side_effect=httpx.ConnectError("down"))
        assert updates.latest_release_info(client, "rickgram/morning-edition") is None


def test_latest_release_info_rejects_a_malformed_repo_without_a_request(client):
    with respx.mock(assert_all_called=False) as router:
        route = router.get(host="api.github.com")
        assert updates.latest_release_info(client, "../../etc?x=y") is None
    assert not route.called


def test_commits_url_is_https_github_or_empty():
    assert updates.commits_url("rickgram/morning-edition") == "https://github.com/rickgram/morning-edition/commits/main"
    assert updates.commits_url("javascript:alert(1)") == ""


# ---- request_update --------------------------------------------------------------------------

def test_request_update_sends_bearer_token_and_image_and_records_the_request_first(client, cfg):
    seen = {}

    def answer(request):
        seen["status"] = updates.load_status(cfg.data_dir)   # state on disk before the response
        return httpx.Response(200, json={"summary": {}})

    with respx.mock(assert_all_called=True) as router:
        route = router.post(f"{URL}/v1/update").mock(side_effect=answer)
        result = updates.request_update(client, URL, TOKEN, IMAGE, cfg.data_dir, now=NOW)
    req = route.calls.last.request
    assert req.headers["authorization"] == f"Bearer {TOKEN}"
    assert dict(req.url.params) == {"image": IMAGE}
    assert seen["status"].update_requested_at == NOW.isoformat() and seen["status"].update_result == "requested"
    assert result.started is True and result.error == ""
    assert updates.load_status(cfg.data_dir).update_result == "started"


@pytest.mark.parametrize("exc", [httpx.ReadError("reset"), httpx.RemoteProtocolError("closed"),
                                 httpx.ReadTimeout("slow")])
def test_a_connection_dropped_after_sending_means_the_update_started(client, cfg, exc):
    with respx.mock() as router:
        router.post(f"{URL}/v1/update").mock(side_effect=exc)
        result = updates.request_update(client, URL, TOKEN, IMAGE, cfg.data_dir, now=NOW)
    assert result.started is True
    assert updates.load_status(cfg.data_dir).update_result == "started"


@pytest.mark.parametrize("response", [httpx.Response(401, text="no"), httpx.Response(500, text="boom"),
                                      httpx.ConnectError("refused " + TOKEN), httpx.ConnectTimeout("slow")])
def test_an_update_that_never_reached_the_updater_is_a_failure(client, cfg, response, caplog):
    caplog.set_level(logging.DEBUG)
    with respx.mock() as router:
        router.post(f"{URL}/v1/update").mock(
            side_effect=response if isinstance(response, Exception) else None,
            return_value=None if isinstance(response, Exception) else response)
        result = updates.request_update(client, URL, TOKEN, IMAGE, cfg.data_dir, now=NOW)
    status = updates.load_status(cfg.data_dir)
    assert result.started is False and result.error
    assert status.update_result == "failed" and status.update_error == result.error
    assert TOKEN not in result.error and TOKEN not in caplog.text
    assert TOKEN not in (cfg.data_dir / "updates.json").read_text()


# ---- state file, run_check -------------------------------------------------------------------

def test_status_round_trips_and_survives_a_missing_or_corrupt_file(tmp_path):
    assert updates.load_status(tmp_path) == updates.UpdateStatus()
    (tmp_path / "updates.json").write_text("{not json")
    assert updates.load_status(tmp_path) == updates.UpdateStatus()
    updates.record(tmp_path, check_result="up_to_date", checked_at="x")
    updates.record(tmp_path, update_result="started")
    status = updates.load_status(tmp_path)
    assert (status.check_result, status.checked_at, status.update_result) == ("up_to_date", "x", "started")
    assert set(json.loads((tmp_path / "updates.json").read_text())) >= {"check_result", "update_result"}


def test_run_check_records_the_result_and_the_latest_commit(client, cfg):
    with respx.mock() as router:
        router.post(f"{URL}/v1/check").mock(return_value=httpx.Response(
            200, json=body(container(update_available=True))))
        router.get(host="api.github.com").mock(return_value=httpx.Response(200, json=COMMIT))
        status = updates.run_check(client, cfg, now=NOW)
    assert status == updates.load_status(cfg.data_dir)
    assert (status.check_result, status.checked_at, status.check_error) == ("update_available", NOW.isoformat(), "")
    assert (status.latest_sha, status.latest_message, status.latest_date) == (
        "0123456", "feat: one-click updates", "2026-10-11")


def test_run_check_records_an_error_and_keeps_going_when_github_is_down(client, cfg):
    with respx.mock() as router:
        router.post(f"{URL}/v1/check").mock(return_value=httpx.Response(500))
        router.get(host="api.github.com").mock(return_value=httpx.Response(403))
        status = updates.run_check(client, cfg, now=NOW)
    assert status.check_result == "error" and status.check_error and status.latest_sha == ""


def test_updater_configured_needs_both_url_and_token(tmp_path):
    base = dict(data_dir=tmp_path, dashboard_password="p", session_secret="s")
    assert updates.configured(AppConfig(**base, watchtower_url=URL, watchtower_token=TOKEN))
    assert not updates.configured(AppConfig(**base, watchtower_url=URL))
    assert not updates.configured(AppConfig(**base, watchtower_token=TOKEN))


def test_format_when_uses_the_papers_timezone():
    assert updates.format_when("2026-10-12T06:00:00+00:00", "America/New_York") == "Oct 12, 2:00 AM"
    assert updates.format_when("", "UTC") == "" and updates.format_when("garbage", "UTC") == ""
