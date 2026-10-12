import time
from collections.abc import Callable

import httpx

ATTEMPTS = 3
BACKOFF_SECONDS = (1, 2)

_sleep: Callable[[float], None] = time.sleep


def get_with_retry(client: httpx.Client, url: str, *, sleep: Callable[[float], None] | None = None,
                   **kwargs) -> httpx.Response:
    """GET with up to three attempts, backing off 1s then 2s, retrying on transport errors
    and 5xx answers. The last attempt's failure surfaces exactly as a plain GET's would:
    a transport error is raised, a 5xx response is returned for the caller to inspect."""
    wait = sleep or _sleep
    for attempt in range(ATTEMPTS):
        last = attempt == ATTEMPTS - 1
        try:
            resp = client.get(url, **kwargs)
        except httpx.TransportError:
            if last:
                raise
        else:
            if resp.status_code < 500 or last:
                return resp
        wait(BACKOFF_SECONDS[attempt])
    raise AssertionError("unreachable")
