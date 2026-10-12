import re
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

GOOD_NEWS = "Good News"
MODEL_CHOICES = [
    ("claude-sonnet-5-5", "Claude Sonnet 5.5 (recommended, about $6.50 a month)"),
    ("claude-opus-5-5", "Claude Opus 5.5 (about $13 a month)"),
]
MASTHEAD_FONTS = ["Pirata One", "Manufacturing Consent", "Grenze Gotisch", "Playfair Display"]


class CalendarLink(BaseModel):
    name: str
    url: str          # private iCal link: stored, fetched, never rendered or logged


class WeatherLocation(BaseModel):
    query: str = ""                 # what the reader typed, e.g. "New York, NY 10001"
    label: str = ""                 # the geocoded name printed on the masthead
    latitude: float | None = None
    longitude: float | None = None


_OLD_LOCATION_KEYS = ("location_query", "location_label", "latitude", "longitude")


class Settings(BaseModel):
    paper_name: str = "Morning Edition"
    masthead_font: str = "Pirata One"
    timezone: str = "UTC"
    weather_locations: list[WeatherLocation] = []   # element 0 is home, the only one used so far
    weather_units: Literal["us", "metric"] = "us"
    expanded_weather: bool = False
    print_time: str = "05:30"
    build_lead_minutes: int = 20
    target_sheets: int = 2
    max_sheets: int = 3
    sections: list[str] = ["Local", "World", "Tech"]
    interests: str = ""
    calendars: list[CalendarLink] = []
    puzzle_mode: Literal["alternate", "sudoku", "crossword"] = "alternate"
    sudoku_difficulty: Literal["easy", "medium", "hard"] = "medium"
    printer_address: str = ""
    auto_print: bool = True
    model: str = "claude-sonnet-5-5"
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    halftone_gamma: float = Field(default=0.5, gt=0)
    auto_update: bool = False
    update_time: str = "02:30"

    @model_validator(mode="before")
    @classmethod
    def _migrate_ical_url(cls, data):
        """Settings saved before multiple calendars held one ical_url; it becomes the first calendar."""
        if isinstance(data, dict) and "ical_url" in data:
            data = dict(data)
            old = str(data.pop("ical_url") or "").strip()
            if old and not data.get("calendars"):
                data["calendars"] = [{"name": "Calendar", "url": old}]
        return data

    @model_validator(mode="before")
    @classmethod
    def _migrate_location(cls, data):
        """Settings saved before the weather list held one flat location; it becomes the home entry."""
        if isinstance(data, dict) and any(k in data for k in _OLD_LOCATION_KEYS):
            data = dict(data)
            old = {k.removeprefix("location_"): data.pop(k, None) for k in _OLD_LOCATION_KEYS}
            if not data.get("weather_locations") and any(v not in (None, "") for v in old.values()):
                data["weather_locations"] = [{"query": old["query"] or "", "label": old["label"] or "",
                                              "latitude": old["latitude"], "longitude": old["longitude"]}]
        return data

    @property
    def home(self) -> WeatherLocation:
        return self.weather_locations[0] if self.weather_locations else WeatherLocation()

    @field_validator("print_time", "update_time")
    @classmethod
    def _hh_mm(cls, v: str, info: ValidationInfo) -> str:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v):
            raise ValueError(f"{info.field_name} must be HH:MM (24 hour)")
        return v

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {v!r}") from exc
        return v

    @field_validator("masthead_font")
    @classmethod
    def _font(cls, v: str) -> str:
        if v not in MASTHEAD_FONTS:
            raise ValueError(f"masthead_font must be one of {MASTHEAD_FONTS}")
        return v

    @field_validator("sections")
    @classmethod
    def _sections(cls, v: list[str]) -> list[str]:
        cleaned = [s.strip() for s in v if s.strip() and s.strip() != GOOD_NEWS]
        if not cleaned:
            raise ValueError("at least one section is required")
        return cleaned

    @model_validator(mode="after")
    def _sheets(self) -> "Settings":
        if not 1 <= self.target_sheets <= self.max_sheets <= 6:
            raise ValueError("need 1 <= target_sheets <= max_sheets <= 6")
        return self
