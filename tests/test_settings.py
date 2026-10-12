import pytest
from pydantic import ValidationError

from morning_edition import db
from morning_edition.settings import Settings, WeatherLocation


def test_defaults_are_generic():
    s = Settings()
    assert s.paper_name == "Morning Edition"
    assert s.masthead_font == "Pirata One"
    assert s.sections == ["Local", "World", "Tech"]
    assert s.model == "claude-sonnet-5-5"
    assert s.effort == "medium"
    assert s.target_sheets == 2 and s.max_sheets == 3
    assert s.weather_locations == [] and s.weather_units == "us"
    assert s.home == WeatherLocation()
    assert s.calendars == [] and s.expanded_weather is False


def test_print_time_must_be_hh_mm():
    with pytest.raises(ValidationError):
        Settings(print_time="5:30am")


def test_update_settings_default_off_at_two_thirty_and_validate_the_time():
    s = Settings()
    assert s.auto_update is False and s.update_time == "02:30"
    assert Settings(auto_update=True, update_time="23:59").update_time == "23:59"
    for bad in ("2:30", "24:00", "02:60", "", "soon"):
        with pytest.raises(ValidationError, match="update_time"):
            Settings(update_time=bad)


def test_timezone_must_exist():
    with pytest.raises(ValidationError):
        Settings(timezone="Mars/Olympus")


def test_max_sheets_not_below_target():
    with pytest.raises(ValidationError):
        Settings(target_sheets=3, max_sheets=2)


def test_sections_cannot_be_empty():
    with pytest.raises(ValidationError):
        Settings(sections=[])


@pytest.mark.parametrize("bad", [0, -0.5])
def test_halftone_gamma_must_be_positive(bad):
    with pytest.raises(ValidationError):
        Settings(halftone_gamma=bad)
    assert Settings(halftone_gamma=0.01).halftone_gamma == 0.01


def test_docker_build_uses_the_pinned_constraints():
    from pathlib import Path

    root = Path(__file__).parent.parent
    pins = (root / "constraints.txt").read_text().splitlines()
    assert pins and all("==" in line for line in pins) and not any("morning" in line.lower() for line in pins)
    dockerfile = (root / "Dockerfile").read_text()
    assert "COPY pyproject.toml constraints.txt" in dockerfile and "-c constraints.txt" in dockerfile


def test_saved_opus_model_is_kept():
    assert Settings.model_validate_json('{"model": "claude-opus-5-5"}').model == "claude-opus-5-5"


OLD_SHAPE = {"paper_name": "Old Paper", "location_query": "New York, NY 10001", "location_label": "New York, New York",
             "latitude": 40.75, "longitude": -73.99, "expanded_weather": True}


def test_old_location_fields_migrate_to_the_home_entry_through_load_settings(conn):
    import json

    conn.execute("INSERT INTO settings (id, data) VALUES (1, ?)", (json.dumps(OLD_SHAPE),))
    s = db.load_settings(conn)
    assert s.weather_locations == [WeatherLocation(query="New York, NY 10001", label="New York, New York",
                                                   latitude=40.75, longitude=-73.99)]
    assert s.home.label == "New York, New York" and s.home.latitude == 40.75
    assert s.paper_name == "Old Paper" and s.expanded_weather is True and s.weather_units == "us"
    dumped = s.model_dump()
    assert not {"location_query", "location_label", "latitude", "longitude"} & set(dumped)
    db.save_settings(conn, s)
    assert db.load_settings(conn) == s     # saving writes the new shape, which loads unchanged


def test_migration_keeps_an_existing_list_and_drops_the_old_keys():
    new = [{"query": "Paris", "label": "Paris, Ile-de-France", "latitude": 48.85, "longitude": 2.35}]
    s = Settings.model_validate({**OLD_SHAPE, "weather_locations": new})
    assert [l.label for l in s.weather_locations] == ["Paris, Ile-de-France"]


def test_blank_old_location_makes_no_home_entry():
    s = Settings.model_validate({"location_query": "", "location_label": "", "latitude": None, "longitude": None})
    assert s.weather_locations == [] and s.home == WeatherLocation()


def test_home_is_the_first_location():
    s = Settings(weather_locations=[WeatherLocation(query="a", label="A"), WeatherLocation(query="b", label="B")])
    assert s.home.label == "A"


def test_weather_units_are_us_or_metric():
    assert Settings(weather_units="metric").weather_units == "metric"
    with pytest.raises(ValidationError):
        Settings(weather_units="kelvin")
