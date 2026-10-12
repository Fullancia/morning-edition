import ipaddress
import socket
import time
from collections.abc import Callable

import httpx

ATTEMPTS = 3
BACKOFF_SECONDS = (1, 2)
MAX_BODY_BYTES = 2_000_000
MAX_IMAGE_BYTES = 10_000_000

_sleep: Callable[[float], None] = time.sleep


class BodyTooLarge(httpx.HTTPError):
    """A response body grew past the cap; callers treat it like any other failed fetch."""


def get_capped(client: httpx.Client, url: str, *, max_bytes: int = MAX_BODY_BYTES, **kwargs) -> httpx.Response:
    """GET that stops reading at `max_bytes` (after decompression) and raises BodyTooLarge instead."""
    with client.stream("GET", url, **kwargs) as resp:
        declared = resp.headers.get("content-length", "")
        if declared.isascii() and declared.isdigit() and int(declared) > max_bytes:
            raise BodyTooLarge(f"response is larger than {max_bytes} bytes")
        body = bytearray()
        for chunk in resp.iter_bytes():
            body += chunk
            if len(body) > max_bytes:
                raise BodyTooLarge(f"response is larger than {max_bytes} bytes")
        headers = [(k, v) for k, v in resp.headers.multi_items()
                   if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")]
        return httpx.Response(resp.status_code, headers=headers, content=bytes(body), request=resp.request,
                              extensions=resp.extensions)


def get_with_retry(client: httpx.Client, url: str, *, sleep: Callable[[float], None] | None = None,
                   max_bytes: int | None = None, **kwargs) -> httpx.Response:
    """GET with up to three attempts, backing off 1s then 2s, retrying on transport errors
    and 5xx answers. The last attempt's failure surfaces exactly as a plain GET's would:
    a transport error is raised, a 5xx response is returned for the caller to inspect.
    With `max_bytes`, a body over the cap raises BodyTooLarge at once (no retry)."""
    wait = sleep or _sleep
    for attempt in range(ATTEMPTS):
        last = attempt == ATTEMPTS - 1
        try:
            resp = (client.get(url, **kwargs) if max_bytes is None
                    else get_capped(client, url, max_bytes=max_bytes, **kwargs))
        except httpx.TransportError:
            if last:
                raise
        else:
            if resp.status_code < 500 or last:
                return resp
        wait(BACKOFF_SECONDS[attempt])
    raise AssertionError("unreachable")


def resolve(host: str) -> list[str]:
    """Every address `host` resolves to; empty when it doesn't resolve."""
    try:
        return [info[4][0] for info in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)]
    except (OSError, UnicodeError):
        return []


def is_public_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


class PublicOnlyTransport(httpx.HTTPTransport):
    """Refuses to connect to private, loopback, link-local, reserved, multicast or unspecified
    addresses. Every redirect hop passes through here, so a public page can't bounce the app
    into the local network. A name that doesn't resolve is let through: the request fails by itself."""

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        try:
            ipaddress.ip_address(host.split("%")[0])
            addresses = [host]
        except ValueError:
            addresses = resolve(host)
        if any(not is_public_address(a) for a in addresses):
            raise httpx.ConnectError("blocked non-public address")
        return super().handle_request(request)


def public_client(retries: int = 3) -> httpx.Client:
    """A client for fetching URLs that came from the outside world (found feeds, article links)."""
    return httpx.Client(transport=PublicOnlyTransport(retries=retries))
