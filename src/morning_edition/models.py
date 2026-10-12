from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel


class FeedItem(BaseModel):
    id: int
    feed_id: int
    section: str
    title: str
    url: str          # as published, used for fetching
    key: str          # normalized url, used for dedupe and history
    summary: str
    published: datetime | None
    source: str
    headlines_only: bool


class Piece(BaseModel):
    item_id: int
    section: str
    kind: Literal["lead", "story", "brief", "good_news"]
    headline: str
    deck: str
    paragraphs: list[str]
    source: str
    key: str
    url: str = ""     # the original article link as published (key is the normalized form)
    image_path: str | None = None


class DayForecast(BaseModel):
    day: date
    high: int
    low: int
    summary: str
    precip_chance: int
    code: int = 3                 # WMO weather_code, picks the icon; 3 (overcast) for hand-built fixtures


NA = "n/a"


class WeatherDetails(BaseModel):
    """Everything but the sunrise and sunset is optional: one missing reading prints "n/a", not nothing."""
    units: Literal["us", "metric"] = "us"
    sunrise: str                  # "7:12 am"
    sunset: str
    precip_24h: float | None = None   # rain and melted snow over the 24 hours ending now: inches or mm
    visibility: float | None = None   # miles or kilometers
    humidity: int | None = None       # percent
    pressure: float | None = None     # inHg or hPa
    uv_index_max: float | None = None   # today's peak
    uv_category: str = ""
    aqi: int | None = None        # omitted when the air quality service fails
    aqi_category: str = ""
    moon_phase: str
    moon_illumination: int        # percent
    moon_waxing: bool = True      # lit side grows (age under half the synodic month)

    @property
    def precip_text(self) -> str:
        if self.precip_24h is None:
            return NA
        return f"{self.precip_24h:.1f} mm" if self.units == "metric" else f"{self.precip_24h:.2f} in"

    @property
    def visibility_text(self) -> str:
        if self.visibility is None:
            return NA
        unit = "km" if self.units == "metric" else "mi"
        return f"10+ {unit}" if self.visibility >= 10 else f"{self.visibility:.1f} {unit}"

    @property
    def humidity_text(self) -> str:
        return NA if self.humidity is None else f"{self.humidity}%"

    @property
    def pressure_text(self) -> str:
        if self.pressure is None:
            return NA
        return f"{self.pressure:.0f} hPa" if self.units == "metric" else f"{self.pressure:.2f} in"

    @property
    def uv_text(self) -> str:
        if self.uv_index_max is None:
            return NA
        return f"{self.uv_index_max:.1f}, {self.uv_category}"


class Weather(BaseModel):
    units: Literal["us", "metric"] = "us"
    current_temp: int | None
    today: DayForecast
    outlook: list[DayForecast]
    wind: str
    details: WeatherDetails | None = None


class Event(BaseModel):
    label: str        # "All day", "Ongoing", or "9:00 am"
    title: str
    all_day: bool
    sort_minute: int  # minutes after local midnight; all-day sorts first with -1


class OnThisDay(BaseModel):
    year: int
    text: str
