import re

import httpx

from morning_edition import db
from morning_edition.settings import WeatherLocation


def test_pages_require_login(client):
    r = client.get("/settings", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_wrong_password_rejected(client):
    r = client.post("/login", data={"password": "nope"})
    assert "Wrong password" in r.text
    assert client.get("/settings", follow_redirects=False).status_code == 303


def test_login_and_logout(client):
    r = client.post("/login", data={"password": "hunter2"}, follow_redirects=False)
    assert r.status_code == 303
    assert client.get("/settings").status_code == 200
    client.post("/logout")
    assert client.get("/settings", follow_redirects=False).status_code == 303


FORM = {"paper_name": "The Rick Times", "masthead_font": "Pirata One", "timezone": "America/New_York",
        "home_location": "", "weather_units": "us", "print_time": "05:30", "target_sheets": "2", "max_sheets": "3",
        "sections": "Local, World, Tech", "interests": "sailing",
        "puzzle_mode": "alternate", "sudoku_difficulty": "medium", "printer_address": "10.0.0.9",
        "auto_print": "on", "model": "claude-opus-5-5", "effort": "medium", "halftone_gamma": "0.5"}


def test_save_settings(authed, app):
    r = authed.post("/settings", data=FORM)
    assert r.status_code == 200 and "Saved" in r.text
    s = db.load_settings(db.connect(app.state.db_path))
    assert s.paper_name == "The Rick Times" and s.sections == ["Local", "World", "Tech"]
    assert s.auto_print is True


def test_invalid_settings_show_error(authed):
    r = authed.post("/settings", data={**FORM, "print_time": "5:30am"})
    assert "print_time" in r.text


SECRET_A = "https://calendar.example.com/secret-token/basic.ics"
SECRET_B = "webcal://calendar.example.com/other-secret/family.ics"


def add_calendar(authed, name, url):
    return authed.post("/calendars", data={"name": name, "url": url}, follow_redirects=False)


def test_calendars_are_listed_by_name_and_never_show_the_url(authed, app):
    r = add_calendar(authed, "Work", SECRET_A)
    assert r.status_code == 303 and r.headers["location"] == "/settings#calendars"
    add_calendar(authed, "Family", SECRET_B)
    s = db.load_settings(db.connect(app.state.db_path))
    assert [(c.name, c.url) for c in s.calendars] == [("Work", SECRET_A), ("Family", SECRET_B)]
    page = authed.get("/settings").text
    assert "Work" in page and "Family" in page and page.count("link saved") == 2
    assert "secret-token" not in page and "other-secret" not in page and "calendar.example.com" not in page
    assert 'action="/calendars/0/delete"' in page and 'action="/calendars/1/delete"' in page
    assert 'name="ical_url"' not in page


def test_calendar_name_defaults_and_bad_links_are_rejected(authed, app):
    add_calendar(authed, "", "  " + SECRET_A + "  ")
    add_calendar(authed, "  ", SECRET_B)
    for bad in ("", "   ", "file:///etc/passwd", "ftp://example.com/x.ics", "javascript:alert(1)",
                "calendar.example.com/x.ics", "http://", "http://[bad"):
        r = add_calendar(authed, "Bad", bad)
        assert r.status_code == 303 and r.headers["location"] == "/settings#calendars"
    s = db.load_settings(db.connect(app.state.db_path))
    assert [(c.name, c.url) for c in s.calendars] == [("Calendar 1", SECRET_A), ("Calendar 2", SECRET_B)]


def test_calendar_can_be_removed(authed, app):
    add_calendar(authed, "Work", SECRET_A)
    add_calendar(authed, "Family", SECRET_B)
    authed.post("/calendars/0/delete")
    authed.post("/calendars/9/delete")  # out of range: ignored
    s = db.load_settings(db.connect(app.state.db_path))
    assert [c.name for c in s.calendars] == ["Family"]
    page = authed.get("/settings").text
    assert "Family" in page and "Work" not in page


def test_calendar_routes_require_login(client):
    for path in ("/calendars", "/calendars/0/delete"):
        r = client.post(path, data={"name": "x", "url": SECRET_A}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login"


def test_saving_settings_preserves_calendars(authed, app):
    add_calendar(authed, "Work", SECRET_A)
    authed.post("/settings", data={**FORM, "calendars": "ignored", "ical_url": "https://evil.example.com/x.ics"})
    s = db.load_settings(db.connect(app.state.db_path))
    assert [(c.name, c.url) for c in s.calendars] == [("Work", SECRET_A)]


def test_location_is_geocoded_on_change(authed, app, network):
    network.get(host="geocoding-api.open-meteo.com").mock(return_value=httpx.Response(200, json={"results": [
        {"name": "New York", "latitude": 40.75, "longitude": -73.99, "timezone": "America/New_York",
         "admin1": "New York", "postcodes": ["10001"]}]}))
    authed.post("/settings", data={**FORM, "home_location": "New York, NY 10001"})
    s = db.load_settings(db.connect(app.state.db_path))
    assert s.home.label == "New York, New York" and round(s.home.latitude, 2) == 40.75
    assert s.home.query == "New York, NY 10001" and len(s.weather_locations) == 1


def test_geocoding_a_new_home_updates_element_zero_and_keeps_the_rest(authed, app, network):
    conn = db.connect(app.state.db_path)
    other = WeatherLocation(query="Paris", label="Paris, Ile-de-France", latitude=48.85, longitude=2.35)
    home = WeatherLocation(query="Old Town", label="Old Town, Maine", latitude=45.0, longitude=-68.7)
    db.save_settings(conn, db.load_settings(conn).model_copy(update={"weather_locations": [home, other]}))
    network.get(host="geocoding-api.open-meteo.com").mock(return_value=httpx.Response(200, json={"results": [
        {"name": "New York", "latitude": 40.75, "longitude": -73.99, "timezone": "America/New_York",
         "admin1": "New York", "postcodes": ["10001"]}]}))
    authed.post("/settings", data={**FORM, "home_location": "New York, NY 10001"})
    s = db.load_settings(db.connect(app.state.db_path))
    assert [l.label for l in s.weather_locations] == ["New York, New York", "Paris, Ile-de-France"]
    # saving again without changing the query does not geocode again or lose anything
    authed.post("/settings", data={**FORM, "home_location": "New York, NY 10001"})
    assert db.load_settings(db.connect(app.state.db_path)).weather_locations == s.weather_locations


def test_unfindable_home_location_shows_an_error_and_keeps_the_old_one(authed, app, network):
    conn = db.connect(app.state.db_path)
    home = WeatherLocation(query="Old Town", label="Old Town, Maine", latitude=45.0, longitude=-68.7)
    db.save_settings(conn, db.load_settings(conn).model_copy(update={"weather_locations": [home]}))
    network.get(host="geocoding-api.open-meteo.com").mock(return_value=httpx.Response(200, json={}))
    r = authed.post("/settings", data={**FORM, "home_location": "Nowhereville"})
    assert "find that location" in r.text
    assert db.load_settings(db.connect(app.state.db_path)).home == home


def test_feed_management(authed, app):
    authed.post("/feeds", data={"url": "https://news.example.com/feed", "section": "Local"})
    authed.post("/feeds", data={"url": "https://news.google.com/rss/search?q=x", "section": "Local"})
    conn = db.connect(app.state.db_path)
    feeds = db.list_feeds(conn)
    assert [f.headlines_only for f in feeds] == [False, True]
    authed.post(f"/feeds/{feeds[0].id}/toggle")
    assert db.list_feeds(conn)[0].enabled is False
    authed.post(f"/feeds/{feeds[0].id}/delete")
    assert len(db.list_feeds(conn)) == 1


def test_google_news_starter_feed(authed, app):
    authed.post("/settings", data={**FORM})
    conn = db.connect(app.state.db_path)
    s = db.load_settings(conn)
    db.save_settings(conn, s.model_copy(update={"weather_locations": [WeatherLocation(label="New York, New York")]}))
    authed.post("/feeds/google-news")
    (feed,) = db.list_feeds(conn)
    assert feed.headlines_only and "New+York" in feed.url and feed.section == "Local"


def test_settings_page_has_good_news_section_option(authed):
    page = authed.get("/settings").text
    assert '<option value="Good News">' in page


def test_settings_page_has_test_print_form(authed):
    assert 'action="/printer/test"' in authed.get("/settings").text


def test_validation_error_does_not_leak_calendar_urls(authed):
    add_calendar(authed, "Work", SECRET_A)
    r = authed.post("/settings", data={**FORM, "print_time": "5:30am"})
    assert "print_time" in r.text and "secret-token" not in r.text
    assert "input_value" not in r.text


def test_model_level_validation_error_does_not_leak_calendar_urls(authed):
    add_calendar(authed, "Work", SECRET_A)
    r = authed.post("/settings", data={**FORM, "target_sheets": "4", "max_sheets": "2"})
    # pydantic's own message dumps the whole settings dict (truncated) as input_value; never render that
    assert "target_sheets" in r.text and "secret-token" not in r.text
    assert "input_value" not in r.text


def test_feed_add_rejects_non_web_urls(authed, app):
    for bad in ("file:///etc/passwd", "ftp://example.com/feed", "javascript:alert(1)", "example.com/feed",
                "http://", "http://[bad"):
        r = authed.post("/feeds", data={"url": bad, "section": "Local"}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/settings#feeds"
    assert db.list_feeds(db.connect(app.state.db_path)) == []
    authed.post("/feeds", data={"url": "  HTTPS://example.com/feed  ", "section": "Local"})
    assert len(db.list_feeds(db.connect(app.state.db_path))) == 1


def test_masthead_font_picker_has_one_radio_per_font_with_live_preview_text(authed):
    from morning_edition.layout.renderer import MASTHEAD_WEIGHTS
    from morning_edition.settings import MASTHEAD_FONTS

    authed.post("/settings", data={**FORM, "masthead_font": "Grenze Gotisch"})
    page = authed.get("/settings").text
    radios = re.findall(r'<input type="radio" name="masthead_font" value="([^"]+)"([^>]*)>', page)
    assert [v for v, _ in radios] == MASTHEAD_FONTS
    assert [v for v, attrs in radios if "checked" in attrs] == ["Grenze Gotisch"]
    previews = re.findall(r'<span class="font-preview" style="([^"]*)">([^<]*)</span>', page)
    assert len(previews) == len(MASTHEAD_FONTS)
    for font, (style, text) in zip(MASTHEAD_FONTS, previews):
        assert text == "The Rick Times"
        assert f"font-family: '{font}'" in style and f"font-weight: {MASTHEAD_WEIGHTS[font]}" in style
    assert '<select name="masthead_font"' not in page


def test_fonts_are_served_without_login(client):
    css = client.get("/fonts/fonts.css")
    assert css.status_code == 200 and "Pirata One" in css.text
    ttf = client.get("/fonts/PirataOne-400.ttf")
    assert ttf.status_code == 200 and len(ttf.content) > 1000
    assert "/fonts/PirataOne-400.ttf" in client.get("/static/dashboard.css").text


def test_timezone_is_a_select_with_us_zones_first(authed):
    page = authed.get("/settings").text
    assert '<select name="timezone"' in page and '<input name="timezone"' not in page
    us = re.search(r'<optgroup label="United States">(.*?)</optgroup>', page, re.S).group(1)
    assert re.findall(r'value="([^"]+)"', us) == [
        "America/New_York", "America/Chicago", "America/Denver", "America/Phoenix",
        "America/Los_Angeles", "America/Anchorage", "Pacific/Honolulu"]
    assert "Eastern (New York)" in us
    assert page.index('value="America/New_York"') < page.index('<optgroup label="All timezones">')
    rest = re.search(r'<optgroup label="All timezones">(.*?)</optgroup>', page, re.S).group(1)
    zones = re.findall(r'value="([^"]+)"', rest)
    assert "Europe/London" in zones and "UTC" in zones
    assert zones == sorted(zones)
    assert not {"America/New_York", "America/Chicago"} & set(zones)
    assert not [z for z in zones if z.startswith(("Etc/", "posix/", "right/")) or ("/" not in z and z != "UTC")]


def test_timezone_saved_zone_is_selected_and_posting_saves(authed, app):
    authed.post("/settings", data={**FORM, "timezone": "America/Denver"})
    assert db.load_settings(db.connect(app.state.db_path)).timezone == "America/Denver"
    page = authed.get("/settings").text
    assert re.findall(r'<option value="([^"]+)" selected>', page).count("America/Denver") == 1
    authed.post("/settings", data={**FORM, "timezone": "Europe/Paris"})
    assert db.load_settings(db.connect(app.state.db_path)).timezone == "Europe/Paris"
    assert '<option value="Europe/Paris" selected>' in authed.get("/settings").text
    assert 'value="America/Denver" selected' not in authed.get("/settings").text


def test_timezone_select_keeps_the_chevron_styling(client):
    css = client.get("/static/dashboard.css").text
    assert "select {" in css and "appearance: none" in css and "right 16px center" in css


def test_expanded_weather_checkbox_saves(authed, app):
    page = authed.get("/settings").text
    assert 'name="expanded_weather"' in page and 'name="expanded_weather" checked' not in page
    authed.post("/settings", data={**FORM, "expanded_weather": "on"})
    assert db.load_settings(db.connect(app.state.db_path)).expanded_weather is True
    assert 'name="expanded_weather" checked' in authed.get("/settings").text
    authed.post("/settings", data=FORM)
    assert db.load_settings(db.connect(app.state.db_path)).expanded_weather is False


def model_select(page):
    select = re.search(r'<select name="model">(.*?)</select>', page, re.S).group(1)
    return re.findall(r'<option value="([^"]+)"( selected)?>([^<]*)</option>', select)


def test_model_is_a_select_with_sonnet_default_and_opus(authed):
    page = authed.get("/settings").text
    assert '<input name="model"' not in page
    assert model_select(page) == [
        ("claude-sonnet-5-5", " selected", "Claude Sonnet 5.5 (recommended, about $6.50 a month)"),
        ("claude-opus-5-5", "", "Claude Opus 5.5 (about $13 a month)")]


def test_saved_opus_stays_selected(authed, app):
    authed.post("/settings", data={**FORM, "model": "claude-opus-5-5"})
    assert db.load_settings(db.connect(app.state.db_path)).model == "claude-opus-5-5"
    selected = [v for v, sel, _ in model_select(authed.get("/settings").text) if sel]
    assert selected == ["claude-opus-5-5"]


def test_unknown_saved_model_is_added_and_selected(authed, app):
    authed.post("/settings", data={**FORM, "model": "claude-haiku-4-5"})
    assert db.load_settings(db.connect(app.state.db_path)).model == "claude-haiku-4-5"
    options = model_select(authed.get("/settings").text)
    assert [v for v, _, _ in options] == ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-4-5"]
    assert [v for v, sel, _ in options if sel] == ["claude-haiku-4-5"]


def section(page, heading):
    """The settings form text from one h2 to the next."""
    start = page.index(f"<h2>{heading}</h2>")
    end = page.find("<h2", start + 1)
    return page[start:end if end != -1 else None]


def test_weather_has_its_own_section_with_home_units_and_expanded(authed):
    page = authed.get("/settings").text
    weather_html = section(page, "Weather")
    assert 'name="home_location"' in weather_html and 'name="weather_units"' in weather_html
    assert 'name="expanded_weather"' in weather_html
    where = section(page, "Where and when")
    assert 'name="timezone"' in where and 'name="print_time"' in where and 'name="auto_print"' in where
    assert 'name="max_sheets"' in where
    assert not {"home_location", "weather_units", "expanded_weather"} & set(re.findall(r'name="([^"]+)"', where))


def test_weather_section_shows_the_geocoded_label(authed, app):
    conn = db.connect(app.state.db_path)
    s = db.load_settings(conn)
    home = WeatherLocation(query="New York, NY", label="New York, New York", latitude=40.75, longitude=-73.99)
    db.save_settings(conn, s.model_copy(update={"weather_locations": [home]}))
    weather_html = section(authed.get("/settings").text, "Weather")
    assert 'value="New York, NY"' in weather_html and "Weather for New York, New York." in weather_html


def test_weather_units_select_saves_and_selects_the_saved_unit(authed, app):
    weather_html = section(authed.get("/settings").text, "Weather")
    assert re.findall(r'<option value="(us|metric)"( selected)?>', weather_html)[0] == ("us", " selected")
    authed.post("/settings", data={**FORM, "weather_units": "metric"})
    assert db.load_settings(db.connect(app.state.db_path)).weather_units == "metric"
    weather_html = section(authed.get("/settings").text, "Weather")
    assert [v for v, sel in re.findall(r'<option value="(us|metric)"( selected)?>', weather_html) if sel] == ["metric"]


def test_invalid_weather_units_are_rejected(authed, app):
    r = authed.post("/settings", data={**FORM, "weather_units": "kelvin"})
    assert "weather_units" in r.text
    assert db.load_settings(db.connect(app.state.db_path)).weather_units == "us"


def test_all_timezones_group_leaves_out_legacy_alias_regions(authed, monkeypatch):
    from morning_edition.web import app as app_module

    legacy = {"US/Eastern", "US/Pacific", "Canada/Pacific", "Brazil/East", "Chile/Continental", "Mexico/General",
              "Antarctica/South_Pole"}
    monkeypatch.setattr(app_module, "available_timezones",
                        lambda: legacy | {"Europe/London", "Antarctica/McMurdo", "America/Toronto", "UTC"})
    app_module._all_timezones.cache_clear()
    try:
        zones = app_module.timezone_choices("UTC")
    finally:
        app_module._all_timezones.cache_clear()
    assert zones == ["America/Toronto", "Antarctica/McMurdo", "Europe/London", "UTC"]


def test_timezone_list_is_computed_once(monkeypatch):
    from morning_edition.web import app as app_module

    calls = []
    monkeypatch.setattr(app_module, "available_timezones", lambda: calls.append(1) or {"Europe/London", "UTC"})
    app_module._all_timezones.cache_clear()
    try:
        app_module.timezone_choices("UTC")
        app_module.timezone_choices("Europe/London")
        assert len(calls) == 1
        assert "Asia/Odd" in app_module.timezone_choices("Asia/Odd")      # a saved zone still shows
        assert "Asia/Odd" not in app_module.timezone_choices("UTC")       # without polluting the cache
    finally:
        app_module._all_timezones.cache_clear()


def test_calendar_names_are_trimmed_capped_and_never_links(authed, app):
    add_calendar(authed, "  Work  ", SECRET_A)
    add_calendar(authed, "x" * 100, SECRET_B)
    for bad in ("https://calendar.example.com/x", "HTTP://x.example.com", "  webcal://cal.example.com/y",
                "http", "WebCal"):
        r = add_calendar(authed, bad, "https://calendar.example.com/ok.ics")
        assert r.status_code == 303 and r.headers["location"] == "/settings#calendars"
    s = db.load_settings(db.connect(app.state.db_path))
    assert [c.name for c in s.calendars] == ["Work", "x" * 60]
    assert "calendar.example.com/x" not in authed.get("/settings").text


def test_default_calendar_names_never_duplicate(authed, app):
    add_calendar(authed, "Calendar 1", SECRET_A)
    add_calendar(authed, "Calendar 3", SECRET_A + "?b")
    add_calendar(authed, "", SECRET_B)
    add_calendar(authed, "", SECRET_B + "?c")
    s = db.load_settings(db.connect(app.state.db_path))
    assert [c.name for c in s.calendars] == ["Calendar 1", "Calendar 3", "Calendar 2", "Calendar 4"]
    authed.post("/calendars/0/delete")
    add_calendar(authed, "   ", SECRET_A + "?d")
    names = [c.name for c in db.load_settings(db.connect(app.state.db_path)).calendars]
    assert names[-1] == "Calendar 1" and len(set(names)) == len(names)


def settings_form_html(page):
    start = page.index('<form method="post" action="/settings"')
    return page[start:page.index("</form>", start)]


def test_settings_form_has_an_id_and_at_least_five_submit_buttons(authed):
    form = settings_form_html(authed.get("/settings").text)
    assert 'id="settings-form"' in form.split(">", 1)[0]
    assert len(re.findall(r"<button>Save settings</button>", form)) >= 5


def test_each_section_ends_with_its_own_save_button(authed):
    form = settings_form_html(authed.get("/settings").text)
    sections = form.split("<h2>")[1:]
    assert len(sections) == 5
    assert all(sec.rstrip().endswith("<button>Save settings</button>") for sec in sections)


def test_unsaved_changes_bar_is_hidden_and_submits_the_main_form(authed):
    page = authed.get("/settings").text
    bar = re.search(r'<div class="savebar"[^>]*>.*?</div>\s*</div>', page, re.S).group(0)
    assert re.search(r"<div[^>]* hidden[ >]", bar.split(">", 1)[0] + ">")
    assert "Unsaved changes" in bar
    assert re.search(r'<button[^>]*type="submit"[^>]*form="settings-form"|<button[^>]*form="settings-form"[^>]*type="submit"', bar)
    # outside the form, so it relies on the form attribute
    assert bar not in settings_form_html(page)
    assert "getElementById('settings-form')" in page


def test_saving_through_the_form_still_works_and_shows_saved(authed, app):
    r = authed.post("/settings", data={**FORM, "paper_name": "The Bar Test"}, follow_redirects=True)
    assert r.status_code == 200 and "Saved." in r.text
    assert 'value="The Bar Test"' in r.text
