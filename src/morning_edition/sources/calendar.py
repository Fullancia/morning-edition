from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx
import icalendar
import recurring_ical_events

from ..models import Event
from .feeds import USER_AGENT


def fetch_ical(client: httpx.Client, url: str) -> str:
    if url.startswith("webcal://"):
        url = "https://" + url.removeprefix("webcal://")
    resp = client.get(url, headers={"User-Agent": USER_AGENT}, timeout=20, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


def _time_label(local: datetime) -> str:
    return local.strftime("%I:%M %p").lstrip("0").lower()


def events_for_day(ical_text: str, day: date, tz: ZoneInfo) -> list[Event]:
    cal = icalendar.Calendar.from_ical(ical_text)
    for subcomp in list(cal.walk()):
        if subcomp.name == "VEVENT" and not subcomp.get("DTSTART"):
            cal.subcomponents.remove(subcomp)
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    events: list[Event] = []
    try:
        recurring = recurring_ical_events.of(cal).between(start, end)
    except (KeyError, AttributeError, ValueError):
        recurring = []
    for ev in recurring:
        if str(ev.get("STATUS", "")).upper() == "CANCELLED":
            continue
        if not ev.get("DTSTART"):
            continue
        title = str(ev.get("SUMMARY", "Untitled")).strip() or "Untitled"
        dt = ev.get("DTSTART").dt
        if isinstance(dt, datetime):
            local = dt.astimezone(tz) if dt.tzinfo else dt.replace(tzinfo=tz)
            if local.date() < day:
                events.append(Event(label="Ongoing", title=title, all_day=False, sort_minute=0))
            else:
                events.append(Event(label=_time_label(local), title=title, all_day=False,
                                    sort_minute=local.hour * 60 + local.minute))
        else:
            events.append(Event(label="All day", title=title, all_day=True, sort_minute=-1))
    return sorted(events, key=lambda e: (e.sort_minute, e.title))
