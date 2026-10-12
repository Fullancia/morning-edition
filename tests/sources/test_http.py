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


# --- public-only transport and size cap ---

import socket

from morning_edition.sources import http as http_mod
from morning_edition.sources.http import BodyTooLarge, get_capped, public_client


@pytest.mark.parametrize("url", ["http://127.0.0.1/feed", "http://[::1]/feed", "http://10.0.0.5/x",
                                 "http://169.254.169.254/latest", "http://0.0.0.0/", "http://224.0.0.1/",
                                 "http://[::ffff:10.0.0.5]/", "http://100.64.0.1/", "http://192.168.1.181/"])
@respx.mock
def test_non_public_literal_addresses_are_refused(url):
    route = respx.get(url__regex=r".*").mock(return_value=httpx.Response(200, text="secret"))
    with public_client() as client, pytest.raises(httpx.ConnectError, match="blocked"):
        client.get(url)
    assert not route.called


@respx.mock
def test_a_name_that_resolves_to_a_private_address_is_refused(monkeypatch):
    monkeypatch.setattr(http_mod, "resolve", lambda host: ["93.184.216.34", "10.0.0.5"])
    route = respx.get("https://sneaky.example.com/").mock(return_value=httpx.Response(200, text="secret"))
    with public_client() as client, pytest.raises(httpx.ConnectError, match="blocked"):
        client.get("https://sneaky.example.com/")
    assert not route.called


@respx.mock
def test_a_redirect_into_the_local_network_is_refused_on_the_hop():
    respx.get("https://example.com/go").mock(
        return_value=httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"}))
    inner = respx.get("http://169.254.169.254/latest/meta-data").mock(return_value=httpx.Response(200, text="x"))
    with public_client() as client, pytest.raises(httpx.ConnectError, match="blocked"):
        client.get("https://example.com/go", follow_redirects=True)
    assert not inner.called


@respx.mock
def test_public_urls_and_unresolvable_names_still_work(monkeypatch):
    respx.get("https://example.com/ok").mock(return_value=httpx.Response(200, text="fine"))
    respx.get("https://gone.example.com/ok").mock(return_value=httpx.Response(200, text="fine too"))
    with public_client() as client:
        assert client.get("https://example.com/ok").text == "fine"
        monkeypatch.setattr(http_mod, "resolve", lambda host: [])  # no answer: the request fails by itself
        assert client.get("https://gone.example.com/ok").text == "fine too"


def test_resolve_returns_nothing_when_lookup_fails(monkeypatch):
    monkeypatch.undo()  # the suite-wide stub would hide the real function

    def fail(*a, **k):
        raise socket.gaierror("no such host")

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    assert http_mod.resolve("x.example.com") == []


@respx.mock
def test_get_capped_stops_at_the_cap_and_passes_small_bodies():
    respx.get("https://example.com/small").mock(return_value=httpx.Response(200, text="hello"))
    respx.get("https://example.com/big").mock(return_value=httpx.Response(200, content=b"x" * 3000))
    respx.get("https://example.com/declared").mock(
        return_value=httpx.Response(200, content=b"x", headers={"content-length": "999999999"}))
    with httpx.Client() as client:
        resp = get_capped(client, "https://example.com/small", max_bytes=100)
        assert resp.text == "hello" and str(resp.url) == "https://example.com/small"
        with pytest.raises(BodyTooLarge):
            get_capped(client, "https://example.com/big", max_bytes=2000)
        with pytest.raises(BodyTooLarge):
            get_capped(client, "https://example.com/declared", max_bytes=2000)


@respx.mock
def test_get_with_retry_with_a_cap_does_not_retry_an_oversize_body():
    route = respx.get("https://example.com/big").mock(return_value=httpx.Response(200, content=b"x" * 3000))
    with httpx.Client() as client, pytest.raises(BodyTooLarge):
        get_with_retry(client, "https://example.com/big", max_bytes=2000)
    assert route.call_count == 1
