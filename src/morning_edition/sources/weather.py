import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from ..models import DayForecast, Weather, WeatherDetails
from .feeds import USER_AGENT
from .http import get_with_retry

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

SYNODIC_MONTH = 29.530588853
NEW_MOON_REF = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
MOON_NAMES = ["New Moon", "Waxing Crescent", "First Quarter", "Waxing Gibbous", "Full Moon",
              "Waning Gibbous", "Last Quarter", "Waning Crescent"]

_WMO = {
    0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Freezing fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Showers", 81: "Showers", 82: "Heavy showers", 85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorms", 96: "Thunderstorms, hail", 99: "Thunderstorms, hail",
}

_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}


def describe(code: int) -> str:
    return _WMO.get(code, "Mixed conditions")


def compass(degrees: float) -> str:
    return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][round(degrees / 45) % 8]


KMH_PER_MPH = 1.609344
UNIT_PARAMS = {
    "us": {"temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "precipitation_unit": "inch"},
    "metric": {"temperature_unit": "celsius", "wind_speed_unit": "kmh", "precipitation_unit": "mm"},
}


def wind_phrase(speed: float, degrees: float, units: str = "us") -> str:
    """The thresholds are in mph; metric speeds (km/h) are converted for the check and shown as given."""
    mph = round(speed / KMH_PER_MPH if units == "metric" else speed)
    if mph < 5:
        return "Calm"
    direction = compass(degrees)
    if mph < 12:
        return f"Light {direction} wind"
    label = f"{round(speed)} {'km/h' if units == 'metric' else 'mph'}"
    if mph < 20:
        return f"Breezy, {direction} wind {label}"
    return f"Windy, {direction} wind {label}"


def aqi_category(aqi: int) -> str:
    for ceiling, name in ((50, "Good"), (100, "Moderate"), (150, "Unhealthy for sensitive groups"),
                          (200, "Unhealthy"), (300, "Very unhealthy")):
        if aqi <= ceiling:
            return name
    return "Hazardous"


def uv_category(uv: float) -> str:
    level = math.floor(uv + 0.5)   # the index is quoted in whole numbers
    for ceiling, name in ((2, "Low"), (5, "Moderate"), (7, "High"), (10, "Very high")):
        if level <= ceiling:
            return name
    return "Extreme"


def _moon_age(when: datetime) -> float:
    return ((when - NEW_MOON_REF).total_seconds() / 86400) % SYNODIC_MONTH


def moon_waxing(when: datetime) -> bool:
    return _moon_age(when) < SYNODIC_MONTH / 2


def moon_phase(when: datetime) -> tuple[str, int]:
    """Phase name and percent illuminated, from the mean synodic month (good to about a day)."""
    age = _moon_age(when)
    illumination = (1 - math.cos(2 * math.pi * age / SYNODIC_MONTH)) / 2
    return MOON_NAMES[round(age / SYNODIC_MONTH * 8) % 8], round(illumination * 100)


def _clock(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%I:%M %p").lstrip("0").lower()


def fetch_air_quality(client: httpx.Client, lat: float, lon: float, tz_name: str) -> int | None:
    """US AQI right now, or None on any failure: air quality is a nicety, never a reason to lose the weather."""
    try:
        resp = get_with_retry(client, AIR_QUALITY_URL, headers={"User-Agent": USER_AGENT}, timeout=20,
                              params={"latitude": lat, "longitude": lon, "current": "us_aqi", "timezone": tz_name})
        resp.raise_for_status()
        value = resp.json()["current"]["us_aqi"]
        return round(value) if value is not None else None
    except Exception:  # noqa: BLE001
        return None


def _reading(read):
    """One optional reading: None when the payload lacks it or holds a null, never an error."""
    try:
        return read()
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _details(data: dict, i: int, now: datetime, tz_name: str, aqi: int | None,
             units: str = "us") -> WeatherDetails | None:
    """Everything the expanded Weather band shows. Without sunrise and sunset there is no band (None);
    any other missing or null reading becomes None on its own and prints as "n/a"."""
    try:
        d = data["daily"]
        sunrise, sunset = _clock(d["sunrise"][i]), _clock(d["sunset"][i])
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    cur = data.get("current") or {}
    metric = units == "metric"
    window_start = now - timedelta(hours=24)

    def rain() -> float:
        hourly = data["hourly"]
        total = sum(mm or 0.0 for t, mm in zip(hourly["time"], hourly["precipitation"])
                    if window_start < datetime.fromisoformat(t) <= now)
        return round(total, 1 if metric else 2)

    uv = _reading(lambda: round(d["uv_index_max"][i], 1))
    noon = datetime.combine(now.date(), datetime.min.time(), tzinfo=ZoneInfo(tz_name)).replace(hour=12)
    phase, lit = moon_phase(noon)
    return WeatherDetails(
        units=units, sunrise=sunrise, sunset=sunset, precip_24h=_reading(rain),
        visibility=_reading(lambda: round(cur["visibility"] / 1000, 1) if metric
                            else round(cur["visibility"] / 1609.34, 1)),
        humidity=_reading(lambda: round(cur["relative_humidity_2m"])),
        pressure=_reading(lambda: round(cur["pressure_msl"]) if metric else round(cur["pressure_msl"] * 0.02953, 2)),
        uv_index_max=uv, uv_category=uv_category(uv) if uv is not None else "",
        aqi=aqi, aqi_category=aqi_category(aqi) if aqi is not None else "",
        moon_phase=phase, moon_illumination=lit, moon_waxing=moon_waxing(noon))


def fetch_weather(client: httpx.Client, lat: float, lon: float, tz_name: str, days: int = 4,
                  expanded: bool = False, units: str = "us") -> Weather:
    daily = ("weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,"
             "wind_speed_10m_max,wind_direction_10m_dominant")
    params = {
        "latitude": lat, "longitude": lon, "daily": daily, "current": "temperature_2m,weather_code",
        **UNIT_PARAMS[units], "timezone": tz_name, "forecast_days": days,
    }
    if expanded:
        params.update({
            # past_days=1 puts yesterday first in every array: today is found by date below
            "past_days": 1, "forecast_days": 6, "hourly": "precipitation",
            "current": "temperature_2m,weather_code,relative_humidity_2m,pressure_msl,visibility,uv_index",
            "daily": daily + ",sunrise,sunset,uv_index_max,precipitation_sum",
        })
    resp = get_with_retry(client, FORECAST_URL, params=params, headers={"User-Agent": USER_AGENT}, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    d = data["daily"]
    now_text = data.get("current", {}).get("time")
    now = datetime.fromisoformat(now_text) if now_text else None
    today_index = 0
    if now is not None and now.date().isoformat() in d["time"]:
        today_index = d["time"].index(now.date().isoformat())
    elif expanded and len(d["time"]) > 1:
        today_index = 1   # past_days=1 puts yesterday first; if today can't be matched, assume position
    forecasts = [
        DayForecast(
            day=date.fromisoformat(d["time"][i]),
            high=round(d["temperature_2m_max"][i]), low=round(d["temperature_2m_min"][i]),
            summary=describe(d["weather_code"][i]),
            precip_chance=round(d["precipitation_probability_max"][i] or 0),
            code=d["weather_code"][i],
        )
        for i in range(today_index, len(d["time"]))
    ]
    current = data.get("current", {}).get("temperature_2m")
    details = None
    if expanded and now is not None:
        details = _details(data, today_index, now, tz_name, fetch_air_quality(client, lat, lon, tz_name), units)
    return Weather(
        units=units, current_temp=round(current) if current is not None else None,
        today=forecasts[0], outlook=forecasts[1:],
        wind=wind_phrase(d["wind_speed_10m_max"][today_index], d["wind_direction_10m_dominant"][today_index], units),
        details=details,
    )


@dataclass
class GeoResult:
    label: str
    latitude: float
    longitude: float
    timezone: str


def geocode(client: httpx.Client, query: str) -> GeoResult | None:
    parts = [p.strip() for p in query.split(",") if p.strip()]
    if not parts:
        return None
    tokens = " ".join(parts[1:]).upper().split()
    resp = client.get(GEOCODE_URL, params={"name": parts[0], "count": 10, "language": "en", "format": "json"},
                      headers={"User-Agent": USER_AGENT}, timeout=20)
    resp.raise_for_status()
    results = resp.json().get("results") or []
    if not results:
        return None

    def score(r: dict) -> int:
        s = 0
        if set(tokens) & set(r.get("postcodes") or []):
            s += 2
        admin = (r.get("admin1") or "").upper()
        if admin and any(t == admin or _STATES.get(t, "").upper() == admin for t in tokens):
            s += 1
        return s

    best = max(results, key=score)  # max keeps the first of equal scores (API order = relevance)
    label = ", ".join(x for x in (best["name"], best.get("admin1")) if x)
    return GeoResult(label, best["latitude"], best["longitude"], best.get("timezone", "UTC"))
