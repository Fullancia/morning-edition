from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx

from morning_edition.sources import weather

FORECAST = {
    "current": {"time": "2026-10-12T05:00", "temperature_2m": 55.6, "weather_code": 0},
    "daily": {
        "time": ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15"],
        "weather_code": [0, 2, 61, 95],
        "temperature_2m_max": [68.0, 69.6, 61.2, 64.0],
        "temperature_2m_min": [51.4, 52.0, 50.0, 47.2],
        "precipitation_probability_max": [3, 10, 80, 60],
        "wind_speed_10m_max": [9.9, 13.9, 15.0, 8.1],
        "wind_direction_10m_dominant": [315, 180, 90, 0],
    },
}

GEO = {"results": [
    {"name": "New York", "latitude": 32.17, "longitude": -95.67, "timezone": "America/Chicago",
     "admin1": "Texas", "country_code": "US", "postcodes": ["75763"]},
    {"name": "New York", "latitude": 40.75, "longitude": -73.99, "timezone": "America/New_York",
     "admin1": "New York", "country_code": "US", "postcodes": ["10001", "10002"]},
]}


def test_describe_and_compass():
    assert weather.describe(0) == "Clear"
    assert weather.describe(61) == "Light rain"
    assert weather.describe(1234) == "Mixed conditions"
    assert weather.compass(315) == "NW"
    assert weather.compass(359) == "N"


def test_wind_phrase():
    assert weather.wind_phrase(3, 0) == "Calm"
    assert weather.wind_phrase(9.9, 315) == "Light NW wind"
    assert weather.wind_phrase(15, 180) == "Breezy, S wind 15 mph"
    assert weather.wind_phrase(25, 90) == "Windy, E wind 25 mph"


def test_wind_phrase_metric_applies_the_thresholds_in_mph():
    assert weather.wind_phrase(7, 0, "metric") == "Calm"                      # 4.3 mph
    assert weather.wind_phrase(12, 315, "metric") == "Light NW wind"          # 7.5 mph
    assert weather.wind_phrase(20, 180, "metric") == "Breezy, S wind 20 km/h"     # 12.4 mph
    assert weather.wind_phrase(30, 90, "metric") == "Breezy, E wind 30 km/h"      # 18.6 mph
    assert weather.wind_phrase(33, 90, "metric") == "Windy, E wind 33 km/h"       # 20.5 mph
    assert weather.wind_phrase(25, 90) == "Windy, E wind 25 mph"                   # us mode unchanged


@respx.mock
def test_fetch_weather_maps_fields():
    route = respx.get(host="api.open-meteo.com", path="/v1/forecast").mock(
        return_value=httpx.Response(200, json=FORECAST))
    with httpx.Client() as client:
        w = weather.fetch_weather(client, 40.75, -73.99, "America/New_York")
    params = route.calls[0].request.url.params
    assert params["temperature_unit"] == "fahrenheit" and params["timezone"] == "America/New_York"
    assert params["wind_speed_unit"] == "mph" and params["precipitation_unit"] == "inch"
    assert w.current_temp == 56
    assert (w.today.high, w.today.low, w.today.summary) == (68, 51, "Clear")
    assert w.today.day == date(2026, 10, 12)
    assert [d.summary for d in w.outlook] == ["Partly cloudy", "Light rain", "Thunderstorms"]
    assert w.wind == "Light NW wind"


@respx.mock
def test_fetch_weather_keeps_the_wmo_code_of_every_day():
    respx.get(host="api.open-meteo.com", path="/v1/forecast").mock(return_value=httpx.Response(200, json=FORECAST))
    with httpx.Client() as client:
        w = weather.fetch_weather(client, 40.75, -73.99, "America/New_York")
    assert [w.today.code] + [d.code for d in w.outlook] == [0, 2, 61, 95]


@respx.mock
def test_geocode_prefers_postcode_and_state_match():
    route = respx.get(host="geocoding-api.open-meteo.com", path="/v1/search").mock(
        return_value=httpx.Response(200, json=GEO))
    with httpx.Client() as client:
        g = weather.geocode(client, "New York, NY 10001")
    assert route.calls[0].request.url.params["name"] == "New York"
    assert g.label == "New York, New York"
    assert g.timezone == "America/New_York"
    assert round(g.latitude, 2) == 40.75


@respx.mock
def test_geocode_no_results():
    respx.get(host="geocoding-api.open-meteo.com", path="/v1/search").mock(return_value=httpx.Response(200, json={}))
    with httpx.Client() as client:
        assert weather.geocode(client, "Nowhereville") is None


@respx.mock
def test_geocode_rejects_admin_less_result():
    results = {"results": [
        {"name": "Paris", "admin1": "Ile-de-France", "latitude": 48.85, "longitude": 2.35, "timezone": "Europe/Paris", "postcodes": ["75001"]},
        {"name": "Paris", "latitude": 36.75, "longitude": -93.72, "timezone": "America/Chicago", "postcodes": []},
    ]}
    respx.get(host="geocoding-api.open-meteo.com", path="/v1/search").mock(return_value=httpx.Response(200, json=results))
    with httpx.Client() as client:
        g = weather.geocode(client, "Paris, 75001")
    assert g.label == "Paris, Ile-de-France"
    assert round(g.latitude, 2) == 48.85


@respx.mock
def test_fetch_weather_retries_a_503():
    payload = {"current": {"temperature_2m": 55.0},
               "daily": {"time": ["2026-10-12"], "weather_code": [0], "temperature_2m_max": [68],
                         "temperature_2m_min": [51], "precipitation_probability_max": [0],
                         "wind_speed_10m_max": [9], "wind_direction_10m_dominant": [315]}}
    route = respx.get(weather.FORECAST_URL).mock(side_effect=[httpx.Response(503), httpx.Response(200, json=payload)])
    with httpx.Client() as client:
        assert weather.fetch_weather(client, 40.7, -73.9, "America/New_York", days=1).current_temp == 55
    assert route.call_count == 2


# ---- expanded weather ----

def expanded_payload(visibility=24140.0, uv=4.8):
    """Open-Meteo with past_days=1: daily starts YESTERDAY (Oct 11), today is Oct 12."""
    days = [f"2026-10-{d}" for d in range(11, 18)]
    hours = [f"2026-10-{d}T{h:02d}:00" for d in range(11, 18) for h in range(24)]
    precip = {h: 0.0 for h in hours}
    precip.update({"2026-10-11T04:00": 0.50,   # before the 24 hour window
                   "2026-10-11T05:00": 0.30,   # exactly 24 hours before: outside the window
                   "2026-10-11T06:00": 0.10, "2026-10-11T23:00": 0.05, "2026-10-12T05:00": 0.02,
                   "2026-10-12T06:00": 0.90})  # the future
    return {
        "current": {"time": "2026-10-12T05:15", "temperature_2m": 55.6, "weather_code": 0,
                    "relative_humidity_2m": 71, "pressure_msl": 1016.4, "visibility": visibility,
                    "uv_index": 0.0},
        "hourly": {"time": hours, "precipitation": [precip[h] for h in hours]},
        "daily": {
            "time": days,
            "weather_code": [95, 0, 2, 61, 95, 3, 1],
            "temperature_2m_max": [99.0, 68.0, 69.6, 61.2, 64.0, 70.1, 72.4],
            "temperature_2m_min": [80.0, 51.4, 52.0, 50.0, 47.2, 49.0, 50.2],
            "precipitation_probability_max": [90, 3, 10, 80, 60, 5, 0],
            "wind_speed_10m_max": [30.0, 9.9, 13.9, 15.0, 8.1, 5.0, 4.0],
            "wind_direction_10m_dominant": [0, 315, 180, 90, 0, 0, 0],
            "sunrise": [f"2026-10-{d}T07:{10 + i:02d}" for i, d in enumerate(range(11, 18))],
            "sunset": [f"2026-10-{d}T18:{10 - i:02d}" for i, d in enumerate(range(11, 18))],
            "uv_index_max": [9.0, uv, 3.0, 2.0, 1.0, 4.0, 4.0],
            "precipitation_sum": [0.9, 0.0, 0.1, 0.4, 0.2, 0.0, 0.0],
        },
    }


AQI_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"


@respx.mock
def test_expanded_request_asks_for_the_extra_fields():
    route = respx.get(host="api.open-meteo.com", path="/v1/forecast").mock(
        return_value=httpx.Response(200, json=expanded_payload()))
    respx.get(AQI_URL).mock(return_value=httpx.Response(200, json={"current": {"us_aqi": 42}}))
    with httpx.Client() as client:
        weather.fetch_weather(client, 40.75, -73.99, "America/New_York", expanded=True)
    p = route.calls[0].request.url.params
    assert p["past_days"] == "1" and p["forecast_days"] == "6" and p["precipitation_unit"] == "inch"
    assert p["hourly"] == "precipitation"
    assert p["current"] == "temperature_2m,weather_code,relative_humidity_2m,pressure_msl,visibility,uv_index"
    for extra in ("sunrise", "sunset", "uv_index_max", "precipitation_sum"):
        assert extra in p["daily"].split(",")


@respx.mock
def test_expanded_finds_today_by_date_not_position():
    respx.get(host="api.open-meteo.com", path="/v1/forecast").mock(
        return_value=httpx.Response(200, json=expanded_payload()))
    respx.get(AQI_URL).mock(return_value=httpx.Response(200, json={"current": {"us_aqi": 42}}))
    with httpx.Client() as client:
        w = weather.fetch_weather(client, 40.75, -73.99, "America/New_York", expanded=True)
    assert w.today.day == date(2026, 10, 12) and (w.today.high, w.today.low) == (68, 51)
    assert w.today.summary == "Clear"
    assert [d.day.day for d in w.outlook] == [13, 14, 15, 16, 17]       # 5 days after today
    assert w.wind == "Light NW wind"                                    # today's wind, not yesterday's


@respx.mock
def test_expanded_details_are_computed_from_the_payload():
    respx.get(host="api.open-meteo.com", path="/v1/forecast").mock(
        return_value=httpx.Response(200, json=expanded_payload()))
    respx.get(AQI_URL).mock(return_value=httpx.Response(200, json={"current": {"us_aqi": 42}}))
    with httpx.Client() as client:
        w = weather.fetch_weather(client, 40.75, -73.99, "America/New_York", expanded=True)
    d = w.details
    assert (d.sunrise, d.sunset) == ("7:11 am", "6:09 pm")
    assert d.precip_24h == 0.17
    assert d.visibility == 15.0 and d.visibility_text == "10+ mi"
    assert d.humidity == 71
    assert d.pressure == 30.01   # 1016.4 hPa
    assert (d.uv_index_max, d.uv_category) == (4.8, "Moderate")
    assert (d.aqi, d.aqi_category) == (42, "Good")
    assert (d.moon_phase, d.moon_illumination) == ("New Moon", 2)    # Oct 12 2026 local noon
    assert d.moon_waxing is True                                     # two days past new
    assert [w.today.code] + [f.code for f in w.outlook] == [0, 2, 61, 95, 3, 1]   # yesterday's 95 is skipped


@respx.mock
def test_expanded_visibility_under_ten_miles_shows_a_decimal():
    respx.get(host="api.open-meteo.com", path="/v1/forecast").mock(
        return_value=httpx.Response(200, json=expanded_payload(visibility=6437.0)))
    respx.get(AQI_URL).mock(return_value=httpx.Response(200, json={"current": {"us_aqi": 42}}))
    with httpx.Client() as client:
        w = weather.fetch_weather(client, 40.75, -73.99, "America/New_York", expanded=True)
    assert w.details.visibility == 4.0 and w.details.visibility_text == "4.0 mi"


@respx.mock
def test_air_quality_request_and_failure_only_omits_aqi():
    respx.get(host="api.open-meteo.com", path="/v1/forecast").mock(
        return_value=httpx.Response(200, json=expanded_payload()))
    aqi = respx.get(AQI_URL).mock(return_value=httpx.Response(500))
    with httpx.Client() as client:
        w = weather.fetch_weather(client, 40.75, -73.99, "America/New_York", expanded=True)
    p = aqi.calls[0].request.url.params
    assert p["current"] == "us_aqi" and p["timezone"] == "America/New_York" and p["latitude"] == "40.75"
    assert w.details is not None and w.details.aqi is None and w.details.aqi_category == ""
    assert w.details.humidity == 71


@respx.mock
def test_air_quality_garbage_only_omits_aqi():
    respx.get(host="api.open-meteo.com", path="/v1/forecast").mock(
        return_value=httpx.Response(200, json=expanded_payload()))
    respx.get(AQI_URL).mock(return_value=httpx.Response(200, json={"current": {"us_aqi": None}}))
    with httpx.Client() as client:
        w = weather.fetch_weather(client, 40.75, -73.99, "America/New_York", expanded=True)
    assert w.details.aqi is None


@respx.mock
def test_not_expanded_changes_nothing_and_skips_air_quality():
    route = respx.get(host="api.open-meteo.com", path="/v1/forecast").mock(
        return_value=httpx.Response(200, json=FORECAST))
    aqi = respx.get(AQI_URL).mock(return_value=httpx.Response(200, json={"current": {"us_aqi": 42}}))
    with httpx.Client() as client:
        w = weather.fetch_weather(client, 40.75, -73.99, "America/New_York")
    p = route.calls[0].request.url.params
    assert "past_days" not in p and "hourly" not in p and p["forecast_days"] == "4"
    assert w.details is None and not aqi.called
    assert [d.day.day for d in w.outlook] == [13, 14, 15]


def _expanded(payload, **kwargs):
    respx.get(host="api.open-meteo.com", path="/v1/forecast").mock(return_value=httpx.Response(200, json=payload))
    respx.get(AQI_URL).mock(return_value=httpx.Response(200, json={"current": {"us_aqi": 42}}))
    with httpx.Client() as client:
        return weather.fetch_weather(client, 40.75, -73.99, "America/New_York", expanded=True, **kwargs)


@respx.mock
def test_expanded_without_sunrise_drops_the_details_but_keeps_the_forecast():
    payload = expanded_payload()
    del payload["daily"]["sunrise"]
    w = _expanded(payload)
    assert w.details is None and w.today.high == 68


@respx.mock
def test_a_null_visibility_does_not_drop_the_rest_of_the_band():
    payload = expanded_payload()
    payload["current"]["visibility"] = None
    d = _expanded(payload).details
    assert d is not None and d.visibility is None and d.visibility_text == "n/a"
    assert d.humidity == 71 and d.pressure == 30.01 and d.uv_index_max == 4.8 and d.precip_24h == 0.17
    assert (d.sunrise, d.sunset) == ("7:11 am", "6:09 pm")


@respx.mock
def test_each_detail_field_degrades_to_na_on_its_own():
    cases = {"relative_humidity_2m": "humidity_text", "pressure_msl": "pressure_text"}
    for key, text in cases.items():
        payload = expanded_payload()
        payload["current"][key] = None
        d = _expanded(payload).details
        assert d is not None and getattr(d, text) == "n/a" and d.sunrise == "7:11 am"
    payload = expanded_payload()
    payload["daily"]["uv_index_max"][1] = None
    d = _expanded(payload).details
    assert d.uv_index_max is None and d.uv_text == "n/a" and d.humidity == 71
    payload = expanded_payload()
    del payload["hourly"]
    d = _expanded(payload).details
    assert d.precip_24h is None and d.precip_text == "n/a" and d.visibility == 15.0


@respx.mock
def test_expanded_uses_index_one_when_the_current_date_is_not_in_the_daily_list():
    payload = expanded_payload()
    payload["current"]["time"] = "2026-10-25T05:15"      # outside daily.time
    w = _expanded(payload)
    assert w.today.day == date(2026, 10, 12) and w.today.high == 68      # today, not yesterday (index 0)
    assert w.wind == "Light NW wind"
    del payload["current"]["time"]
    assert _expanded(payload).today.day == date(2026, 10, 12)


@respx.mock
def test_plain_forecast_without_a_current_time_still_starts_at_index_zero():
    payload = {**FORECAST, "current": {"temperature_2m": 55.6}}
    respx.get(host="api.open-meteo.com", path="/v1/forecast").mock(return_value=httpx.Response(200, json=payload))
    with httpx.Client() as client:
        assert weather.fetch_weather(client, 40.75, -73.99, "America/New_York").today.day == date(2026, 10, 12)


@pytest.mark.parametrize("aqi,name", [(0, "Good"), (50, "Good"), (51, "Moderate"), (100, "Moderate"),
                                      (101, "Unhealthy for sensitive groups"), (150, "Unhealthy for sensitive groups"),
                                      (151, "Unhealthy"), (200, "Unhealthy"), (201, "Very unhealthy"),
                                      (300, "Very unhealthy"), (301, "Hazardous"), (500, "Hazardous")])
def test_aqi_categories(aqi, name):
    assert weather.aqi_category(aqi) == name


@pytest.mark.parametrize("uv,name", [(0, "Low"), (2.4, "Low"), (2.6, "Moderate"), (5.0, "Moderate"),
                                     (6.0, "High"), (7.4, "High"), (8.0, "Very high"), (10.4, "Very high"),
                                     (11.0, "Extreme"), (13.2, "Extreme")])
def test_uv_categories(uv, name):
    assert weather.uv_category(uv) == name


def test_moon_phase_known_dates():
    ny = ZoneInfo("America/New_York")
    noon = lambda y, m, d: datetime(y, m, d, 12, tzinfo=ny)  # noqa: E731
    assert weather.moon_phase(noon(2024, 4, 8)) == ("New Moon", 0)      # the total solar eclipse
    assert weather.moon_phase(noon(2024, 4, 23)) == ("Full Moon", 100)
    assert weather.moon_phase(noon(2026, 10, 10)) == ("New Moon", 1)
    assert weather.moon_phase(noon(2026, 10, 18)) == ("First Quarter", 49)
    assert weather.moon_phase(noon(2026, 10, 3)) == ("Last Quarter", 54)


def test_moon_waxing_is_true_in_the_first_half_of_the_month():
    ref = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    month = 29.530588853
    assert weather.moon_waxing(ref + timedelta(days=month * 0.25))
    assert not weather.moon_waxing(ref + timedelta(days=month * 0.75))
    assert not weather.moon_waxing(ref + timedelta(days=month * 0.5 + 0.01))
    ny = ZoneInfo("America/New_York")
    assert weather.moon_waxing(datetime(2026, 10, 18, 12, tzinfo=ny))      # first quarter
    assert not weather.moon_waxing(datetime(2026, 10, 3, 12, tzinfo=ny))   # last quarter


def test_moon_phase_buckets_follow_the_synodic_month():
    ref = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    month = 29.530588853
    expected = ["New Moon", "Waxing Crescent", "First Quarter", "Waxing Gibbous", "Full Moon",
                "Waning Gibbous", "Last Quarter", "Waning Crescent"]
    for i, name in enumerate(expected):
        assert weather.moon_phase(ref + timedelta(days=month * i / 8))[0] == name
    assert weather.moon_phase(ref + timedelta(days=month * 0.999))[0] == "New Moon"   # wraps around
    assert weather.moon_phase(ref + timedelta(days=month * 0.25)) == ("First Quarter", 50)
    assert weather.moon_phase(ref + timedelta(days=month * 3 + month * 0.5)) == ("Full Moon", 100)


# ---- metric units ----

@respx.mock
def test_metric_units_request_the_metric_parameters():
    route = respx.get(host="api.open-meteo.com", path="/v1/forecast").mock(
        return_value=httpx.Response(200, json={**FORECAST, "daily": {**FORECAST["daily"],
                                                                      "wind_speed_10m_max": [20.0, 5.0, 5.0, 5.0]}}))
    with httpx.Client() as client:
        w = weather.fetch_weather(client, 40.75, -73.99, "America/New_York", units="metric")
    p = route.calls[0].request.url.params
    assert (p["temperature_unit"], p["wind_speed_unit"], p["precipitation_unit"]) == ("celsius", "kmh", "mm")
    assert w.units == "metric" and w.wind == "Breezy, NW wind 20 km/h"


@respx.mock
def test_metric_expanded_details_use_mm_km_and_hpa():
    route = respx.get(host="api.open-meteo.com", path="/v1/forecast").mock(
        return_value=httpx.Response(200, json=expanded_payload(visibility=6437.0)))
    respx.get(AQI_URL).mock(return_value=httpx.Response(200, json={"current": {"us_aqi": 42}}))
    with httpx.Client() as client:
        w = weather.fetch_weather(client, 40.75, -73.99, "America/New_York", expanded=True, units="metric")
    p = route.calls[0].request.url.params
    assert p["precipitation_unit"] == "mm" and p["temperature_unit"] == "celsius"
    d = w.details
    assert d.units == "metric"
    assert d.precip_24h == 0.2 and d.precip_text == "0.2 mm"         # the (mock) API already answered in mm
    assert d.visibility == 6.4 and d.visibility_text == "6.4 km"
    assert d.pressure == 1016 and d.pressure_text == "1016 hPa"


def test_us_detail_text():
    from morning_edition.models import WeatherDetails
    d = WeatherDetails(sunrise="7:12 am", sunset="6:08 pm", precip_24h=0.17, visibility=15.0, humidity=71,
                       pressure=30.01, uv_index_max=4.8, uv_category="Moderate", moon_phase="New Moon",
                       moon_illumination=2)
    assert (d.precip_text, d.visibility_text, d.pressure_text) == ("0.17 in", "10+ mi", "30.01 in")
