import httpx
import pytest
import respx

from morning_edition.sources.http import get_with_retry


@respx.mock
def test_503_then_200_succeeds_after_a_backoff():
    route = respx.get("https://example.com/x").mock(side_effect=[httpx.Response(503), httpx.Response(200, text="ok")])
    naps = []
    with httpx.Client() as client:
        resp = get_with_retry(client, "https://example.com/x", sleep=naps.append)
    assert resp.text == "ok" and route.call_count == 2 and naps == [1]


@respx.mock
def test_backoff_is_one_then_two_seconds():
    route = respx.get("https://example.com/x").mock(side_effect=[
        httpx.ConnectError("no"), httpx.ReadTimeout("slow"), httpx.Response(200, text="ok")])
    naps = []
    with httpx.Client() as client:
        assert get_with_retry(client, "https://example.com/x", sleep=naps.append).text == "ok"
    assert route.call_count == 3 and naps == [1, 2]


@respx.mock
def test_three_timeouts_fail_as_a_plain_get_would():
    route = respx.get("https://example.com/x").mock(side_effect=httpx.ReadTimeout("slow"))
    with httpx.Client() as client, pytest.raises(httpx.ReadTimeout):
        get_with_retry(client, "https://example.com/x", sleep=lambda s: None)
    assert route.call_count == 3


@respx.mock
def test_persistent_5xx_is_returned_for_the_caller_to_judge():
    route = respx.get("https://example.com/x").mock(return_value=httpx.Response(502))
    with httpx.Client() as client:
        resp = get_with_retry(client, "https://example.com/x", sleep=lambda s: None)
    assert resp.status_code == 502 and route.call_count == 3


@respx.mock
def test_4xx_is_not_retried():
    route = respx.get("https://example.com/x").mock(return_value=httpx.Response(404))
    with httpx.Client() as client:
        assert get_with_retry(client, "https://example.com/x", sleep=lambda s: None).status_code == 404
    assert route.call_count == 1


def test_default_services_retry_connections():
    from morning_edition.web.app import default_services

    svc = default_services()
    assert svc.http._transport._pool._retries == 3
