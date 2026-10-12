import re
from datetime import date

import httpx

from ..models import OnThisDay
from .feeds import USER_AGENT

_NOTE = re.compile(r"\s*\((?:pictured|depicted|shown|illustrated)\)", re.IGNORECASE)


def fetch_on_this_day(client: httpx.Client, day: date, count: int = 3) -> list[OnThisDay]:
    url = ("https://api.wikimedia.org/feed/v1/wikipedia/en/onthisday/selected/"
           f"{day.month:02d}/{day.day:02d}")
    try:
        resp = client.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
        resp.raise_for_status()
        selected = resp.json().get("selected") or []
        items = sorted(
            (OnThisDay(year=int(s["year"]), text=_NOTE.sub("", s["text"]).strip())
             for s in selected if "year" in s and s.get("text")),
            key=lambda i: i.year,
        )
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        return []
    if count <= 1:
        return items[:count]
    if len(items) <= count:
        return items
    step = (len(items) - 1) / (count - 1)
    return [items[round(i * step)] for i in range(count)]
