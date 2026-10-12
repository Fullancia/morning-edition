from datetime import date
from zoneinfo import ZoneInfo

import httpx
import respx

from morning_edition.sources import calendar

NY = ZoneInfo("America/New_York")

ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:1
DTSTART;TZID=America/New_York:20261012T090000
DTEND;TZID=America/New_York:20261012T100000
SUMMARY:Coffee at the market
END:VEVENT
BEGIN:VEVENT
UID:2
DTSTART;VALUE=DATE:20261011
DTEND;VALUE=DATE:20261014
SUMMARY:Boat show weekend
END:VEVENT
BEGIN:VEVENT
UID:3
DTSTART:20261012T180000Z
DTEND:20261012T190000Z
SUMMARY:Call with London
END:VEVENT
BEGIN:VEVENT
UID:4
DTSTART;TZID=America/New_York:20260914T140000
DTEND;TZID=America/New_York:20260914T150000
RRULE:FREQ=WEEKLY;BYDAY=MO
SUMMARY:Weekly standup
END:VEVENT
BEGIN:VEVENT
UID:5
DTSTART;TZID=America/New_York:20261012T120000
DTEND;TZID=America/New_York:20261012T130000
STATUS:CANCELLED
SUMMARY:Cancelled lunch
END:VEVENT
BEGIN:VEVENT
UID:6
DTSTART;TZID=America/New_York:20261013T090000
DTEND;TZID=America/New_York:20261013T100000
SUMMARY:Tomorrow thing
END:VEVENT
END:VCALENDAR
"""


def test_events_for_day_handles_all_day_recurring_and_timezones():
    events = calendar.events_for_day(ICS, date(2026, 10, 12), NY)
    assert [(e.label, e.title) for e in events] == [
        ("All day", "Boat show weekend"),
        ("9:00 am", "Coffee at the market"),
        ("2:00 pm", "Call with London"),
        ("2:00 pm", "Weekly standup"),
    ]


def test_events_for_day_empty_day():
    assert calendar.events_for_day(ICS, date(2026, 12, 25), NY) == []


@respx.mock
def test_fetch_ical_accepts_webcal():
    respx.get("https://calendar.example.com/private.ics").mock(return_value=httpx.Response(200, text=ICS))
    with httpx.Client() as client:
        assert "VCALENDAR" in calendar.fetch_ical(client, "webcal://calendar.example.com/private.ics")


def test_events_for_day_skips_events_without_dtstart():
    ics_with_bad_event = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:1
DTSTART;TZID=America/New_York:20261012T090000
DTEND;TZID=America/New_York:20261012T100000
SUMMARY:Good event
END:VEVENT
BEGIN:VEVENT
UID:2
DTEND;TZID=America/New_York:20261012T100000
SUMMARY:Bad event with no DTSTART
END:VEVENT
END:VCALENDAR
"""
    events = calendar.events_for_day(ics_with_bad_event, date(2026, 10, 12), NY)
    assert [(e.label, e.title) for e in events] == [("9:00 am", "Good event")]
