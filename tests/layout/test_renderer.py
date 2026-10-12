import dataclasses
import random
import re
from datetime import date

import pytest
from PIL import Image

from morning_edition.layout import geometry as g
from morning_edition.layout import renderer
from markupsafe import Markup

from morning_edition.layout.measure import extents, render_document, widths
from morning_edition.models import DayForecast, Event, OnThisDay, Piece, Weather, WeatherDetails
from morning_edition.puzzles import crossword, sudoku
from morning_edition.settings import Settings, WeatherLocation

SENT = "The council said the work would begin in the spring after a long and careful debate. "
SECTIONS = ["Local", "World", "Tech"]
FOLD_CLASSES = {"headline", "photo", "puzzle-grid"}


def piece(i, kind="story", section="Local", n=4, headline=None):
    sentences = 2 if kind == "brief" else 3
    return Piece(
        item_id=i, section=section, kind=kind,
        headline=headline or f"Test headline number {i} for the edition",
        deck="" if kind == "brief" else "A short deck sentence for the story.",
        paragraphs=[SENT * sentences for _ in range(n)], source="Example Gazette",
        key=f"https://example.com/{i}",
    )


@pytest.fixture
def base(tmp_path):
    return renderer.EditionInput(
        settings=Settings(paper_name="The Test Times", weather_locations=[WeatherLocation(label="Testville")]),
        day=date(2026, 10, 12), number=7, work_dir=tmp_path, weather=None, events=[],
        on_this_day=[OnThisDay(year=1492, text="Columbus reached the Bahamas.")],
        lead=piece(1, "lead", n=8), lead_image=None,
        good_news=piece(2, "good_news", "Good News", n=6),
        primary=[piece(i, "brief" if i % 3 == 0 else "story", SECTIONS[i % 3], n=1 if i % 3 == 0 else 4)
                 for i in range(3, 18)],
        reserves=[piece(i, "brief", "Tech", n=1) for i in range(20, 26)],
        also=[],
        puzzle=renderer.PuzzleView(kind="sudoku", sudoku=sudoku.generate(random.Random(1), "easy")),
        answers=None,
    )


def assert_fold_clear(html):
    doc = render_document(html)
    for e in extents(doc, FOLD_CLASSES):
        assert not (e.top < g.FOLD < e.bottom), f"{e.cls} '{e.text}' crosses the fold on page {e.page + 1}"
    return doc


def test_even_pages_and_fold(base):
    r = renderer.render_edition(base)
    assert r.pages % 2 == 0 and 4 <= r.pages <= 6
    doc = assert_fold_clear(r.html)
    assert len(doc.pages) == r.pages
    assert r.pdf.startswith(b"%PDF")
    assert "The Test Times" in r.html
    assert {p.key for p in r.placed} >= {"https://example.com/1", "https://example.com/2"}


def test_long_headline_stays_above_fold(base):
    long = " ".join(["Harbor"] * 26)
    r = renderer.render_edition(dataclasses.replace(base, lead=piece(1, "lead", n=8, headline=long)))
    doc = assert_fold_clear(r.html)
    heads = [e for e in extents(doc, {"lead-headline"}) if e.page == 0]
    assert heads and heads[0].bottom < g.FOLD


def test_quiet_day(base):
    r = renderer.render_edition(dataclasses.replace(base, lead=None, good_news=None, primary=[], reserves=[]))
    assert r.pages == 2
    assert "A Quiet News Day" in r.html


def test_lead_photo_is_halftoned_and_above_fold(base, tmp_path):
    src = tmp_path / "lead.jpg"
    Image.new("RGB", (1200, 800), (120, 120, 120)).save(src)
    r = renderer.render_edition(dataclasses.replace(base, lead_image=src))
    assert (tmp_path / "lead-halftone.png").exists()
    doc = assert_fold_clear(r.html)
    photos = [e for e in extents(doc, {"photo"}) if e.page == 0]
    assert photos and all(e.bottom < g.FOLD for e in photos)


def test_overflow_drops_and_respects_max(base):
    many = [piece(i, "story", "World", n=3) for i in range(100, 140)]
    settings = base.settings.model_copy(update={"target_sheets": 2, "max_sheets": 2})
    r = renderer.render_edition(dataclasses.replace(base, settings=settings, primary=many))
    assert r.pages == 4
    assert r.dropped


def test_crossword_clues_fit_and_answers_render(base):
    rng = random.Random(4)
    pattern = crossword.generate_pattern(rng)
    slots = crossword.number_slots(pattern)
    grid = [row[:] for row in pattern]
    for slot in slots:
        slot.answer = "A" * len(slot.cells)
        for r, c in slot.cells:
            grid[r][c] = "A"
    fill = crossword.CrosswordFill(grid=["".join(row) for row in grid], slots=slots)
    clues = {(s.number, s.direction): "A fair clue about thirty chars" for s in slots}
    answers = {"kind": "sudoku", "solution": sudoku.generate(random.Random(2), "easy").solution}
    r = renderer.render_edition(dataclasses.replace(
        base, puzzle=renderer.PuzzleView(kind="crossword", crossword=fill, clues=clues), answers=answers))
    doc = assert_fold_clear(r.html)
    last = len(doc.pages) - 1
    boxes = [e for e in extents(doc, {"clue"}) if e.page == last]
    assert len(boxes) == len(slots)
    assert all(e.right <= g.PAGE_W - g.MARGIN_X + 0.01 for e in boxes)
    assert all(e.bottom <= g.PAGE_H - g.MARGIN_Y + 0.01 for e in boxes)
    assert "Yesterday" in r.html


def test_test_page_renders():
    pdf = renderer.render_test_page(Settings(paper_name="The Rick Times"), "Oct 12, 2026 9:00 am")
    assert pdf.startswith(b"%PDF")


def forecast(day, summary="Partly sunny", code=2):
    return DayForecast(day=date(2026, 10, day), high=64, low=47, summary=summary, precip_chance=10, code=code)


def test_sidebar_never_clips_and_trims_events(base):
    weather = Weather(current_temp=58, today=forecast(12), wind="Wind NW 8 mph",
                      outlook=[forecast(13, "Showers"), forecast(14, "Sunny"), forecast(15, "Cloudy")])
    events = [Event(label="9:00 am", title=f"Calendar entry number {i} with a long wrapping title",
                    all_day=False, sort_minute=540 + i) for i in range(9)]
    r = renderer.render_edition(dataclasses.replace(base, weather=weather, events=events))
    doc = assert_fold_clear(r.html)
    rows = [e for e in extents(doc, {"outlook-row", "cal-row"}) if e.page == 0]
    assert rows and all(e.bottom <= g.FOLD for e in rows)
    assert "64&deg; / 47&deg;" in r.html            # the weather ear
    assert re.search(r"and \d+ more", r.html)       # events were trimmed


def test_sidebar_keeps_outlook_when_it_fits(base):
    weather = Weather(current_temp=58, today=forecast(12), wind="Wind NW 8 mph",
                      outlook=[forecast(13), forecast(14), forecast(15)])
    r = renderer.render_edition(dataclasses.replace(base, weather=weather, events=[]))
    doc = render_document(r.html)
    assert len([e for e in extents(doc, {"outlook-row"}) if e.page == 0]) == 3
    assert "Nothing on the calendar." in r.html


def test_also_key_is_not_reported_as_dropped(base):
    from morning_edition.models import FeedItem
    also = [FeedItem(id=i, feed_id=1, section="Local", title=f"Item {i}", url="u", key=f"k{i}", summary="",
                     published=None, source="Gazette", headlines_only=True) for i in range(3)]
    many = [piece(i, "story", "World", n=3) for i in range(100, 140)]
    settings = base.settings.model_copy(update={"target_sheets": 2, "max_sheets": 2})
    r = renderer.render_edition(dataclasses.replace(base, settings=settings, primary=many, also=also))
    assert "also" not in r.dropped


def test_render_result_reports_the_also_items_that_were_placed(base):
    from morning_edition.models import FeedItem
    also = [FeedItem(id=i, feed_id=1, section="Local", title=f"Item {i}", url=f"https://e.com/{i}", key=f"k{i}",
                     summary="", published=None, source="Gazette", headlines_only=True) for i in range(3)]
    r = renderer.render_edition(dataclasses.replace(base, also=also))
    assert [i.title for i in r.also] == ["Item 0", "Item 1", "Item 2"]
    assert renderer.render_edition(base).also == []


def test_sudoku_is_compact_with_label_and_answers_to_its_right(base):
    answers = {"kind": "sudoku", "solution": sudoku.generate(random.Random(2), "easy").solution}
    r = renderer.render_edition(dataclasses.replace(base, answers=answers))
    doc = assert_fold_clear(r.html)
    last = len(doc.pages) - 1
    (grid,) = [e for e in extents(doc, {"puzzle-grid"}) if e.page == last]
    assert g.SUDOKU_CELL == 0.27
    assert abs((grid.right - grid.left) - 9 * g.SUDOKU_CELL) < 0.06     # about 2.43in square
    assert abs((grid.bottom - grid.top) - 9 * g.SUDOKU_CELL) < 0.06
    assert grid.top >= g.FOLD and grid.bottom <= g.PAGE_H - g.MARGIN_Y   # bottom half of the back page
    (side,) = [e for e in extents(doc, {"puzzle-side"}) if e.page == last]
    (box,) = [e for e in extents(doc, {"answers-box"}) if e.page == last]
    assert side.left >= grid.right and box.left >= grid.right
    assert box.bottom <= g.PAGE_H - g.MARGIN_Y
    assert "Yesterday's sudoku" in r.html
    css = (renderer.TEMPLATES / "edition.css").read_text()
    assert "font-size: 13pt" in css and "width: 0.27in" in css


# ---- expanded weather band ----

def details(**over):
    d = dict(sunrise="7:12 am", sunset="6:08 pm", precip_24h=0.17, visibility=15.0, humidity=71,
             pressure=30.01, uv_index_max=4.8, uv_category="Moderate", aqi=42, aqi_category="Good",
             moon_phase="Waxing Crescent", moon_illumination=23)
    d.update(over)
    return WeatherDetails(**d)


def expanded_weather(**over):
    return Weather(current_temp=58, today=forecast(12), wind="Light NW wind", details=details(**over),
                   outlook=[forecast(13, "Showers", 81), forecast(14, "Sunny", 0), forecast(15, "Cloudy", 3),
                            forecast(16, "Light rain", 61), forecast(17, "Thunderstorms", 95)])


def expanded_input(base, **settings_over):
    settings = base.settings.model_copy(update={"expanded_weather": True, **settings_over})
    return dataclasses.replace(base, settings=settings, weather=expanded_weather())


BAND_LABELS = ["Sunrise / Sunset", "Moon phase", "Rain past 24 h", "Humidity", "Pressure", "Visibility",
               "UV index max", "Air quality"]


def test_weather_band_prints_on_page_two_with_all_details_and_five_days(base):
    r = renderer.render_edition(expanded_input(base))
    doc = assert_fold_clear(r.html)
    assert r.pages % 2 == 0
    (band,) = [e for e in extents(doc, {"weather-band"})]
    assert band.page == 1 and band.top >= g.INSIDE_Y - 0.01
    assert band.bottom <= g.INSIDE_Y + g.WEATHER_BAND_H + 0.01 and band.bottom < g.FOLD - 1.0
    assert g.WEATHER_BAND_H <= 2.2
    for label in BAND_LABELS:
        assert label in r.html
    for value in ("7:12 am / 6:08 pm", "Waxing Crescent, 23%", "0.17 in", "71%", "30.01 in", "10+ mi",
                  "4.8, Moderate", "42, Good"):
        assert value in r.html, value
    days = [e for e in extents(doc, {"wx-day"}) if e.page == 1]
    assert len(days) == 5 and all(band.top <= e.top and e.bottom <= band.bottom + 0.01 for e in days)
    cells = [e for e in extents(doc, {"wx-cell"}) if e.page == 1]
    assert len(cells) == 8 and all(e.bottom <= band.bottom + 0.01 and e.right <= band.right + 0.01 for e in cells)
    assert [d for d in ("Tue", "Wed", "Thu", "Fri", "Sat") if d in r.html]   # Oct 13 to 17, 2026


def test_weather_band_pushes_page_two_columns_below_it(base):
    r = renderer.render_edition(expanded_input(base))
    doc = render_document(r.html)
    band = next(e for e in extents(doc, {"weather-band"}))
    placed = [e for e in extents(doc, {"piece", "also"}) if e.page == 1]
    assert placed and all(e.top >= band.bottom - 0.01 for e in placed)
    rules = [e for e in extents(doc, {"vrule"}) if e.page == 1]
    assert rules and all(e.top >= band.bottom - 0.01 for e in rules)


def test_weather_band_is_in_the_front_page_index(base):
    r = renderer.render_edition(expanded_input(base))
    assert "<b>Weather</b> 2" in r.html
    off = renderer.render_edition(dataclasses.replace(expanded_input(base), settings=base.settings))
    assert "<b>Weather</b> 2" not in off.html


def test_expanded_off_changes_nothing(base):
    plain = renderer.render_edition(dataclasses.replace(base, weather=expanded_weather()))
    assert "weather-band" not in plain.html and "Air quality" not in plain.html
    without_details = expanded_weather().model_copy(update={"details": None})
    same = renderer.render_edition(dataclasses.replace(base, weather=without_details))
    assert plain.html == same.html and plain.pages == same.pages


def test_expanded_without_details_or_inside_pages_prints_no_band(base):
    no_details = dataclasses.replace(expanded_input(base), weather=expanded_weather().model_copy(update={"details": None}))
    assert "Air quality" not in renderer.render_edition(no_details).html
    one_sheet = expanded_input(base, target_sheets=1, max_sheets=1)
    r = renderer.render_edition(one_sheet)
    assert "Air quality" not in r.html and r.pages == 2


def test_quiet_day_with_expanded_weather_still_prints_the_band(base):
    quiet = dataclasses.replace(expanded_input(base), lead=None, good_news=None, primary=[], reserves=[])
    r = renderer.render_edition(quiet)
    doc = assert_fold_clear(r.html)
    assert r.pages % 2 == 0 and r.pages == 4
    assert any(e.page == 1 for e in extents(doc, {"weather-band"}))
    assert "Air quality" in r.html


def test_band_handles_missing_air_quality_and_long_categories(base):
    inp = dataclasses.replace(expanded_input(base), weather=expanded_weather(aqi=None, aqi_category=""))
    r = renderer.render_edition(inp)
    assert "Air quality" in r.html and "n/a" in r.html
    long = dataclasses.replace(expanded_input(base), weather=expanded_weather(
        aqi=120, aqi_category="Unhealthy for sensitive groups", moon_phase="Waning Crescent", visibility=4.0))
    doc = render_document(renderer.render_edition(long).html)
    band = next(e for e in extents(doc, {"weather-band"}))
    assert all(e.bottom <= band.bottom + 0.01 for e in extents(doc, {"wx-cell"}) if e.page == 1)


def test_masthead_and_folio_use_the_home_label(base):
    r = renderer.render_edition(base)
    assert r.html.count("Testville") >= 2


def test_metric_units_render_celsius_kmh_mm_km_and_hpa(base):
    wx = Weather(current_temp=14, today=forecast(12), wind="Breezy, NW wind 20 km/h", units="metric",
                 details=details(units="metric", precip_24h=4.3, visibility=8.0, pressure=1016),
                 outlook=[forecast(13, "Showers"), forecast(14, "Sunny"), forecast(15, "Cloudy"),
                          forecast(16, "Light rain"), forecast(17, "Thunderstorms")])
    settings = base.settings.model_copy(update={"expanded_weather": True, "weather_units": "metric"})
    r = renderer.render_edition(dataclasses.replace(base, settings=settings, weather=wx))
    assert "\u00b0C" in r.html and "km/h" in r.html
    for value in ("4.3 mm", "1016 hPa", "8.0 km"):
        assert value in r.html, value
    assert "&deg;" not in r.html and "30.01 in" not in r.html
    assert "&deg;" in renderer.render_edition(dataclasses.replace(base, weather=expanded_weather())).html


def test_front_sidebar_shows_at_most_three_outlook_rows_with_the_five_day_outlook(base):
    r = renderer.render_edition(expanded_input(base))
    doc = render_document(r.html)
    rows = [e for e in extents(doc, {"outlook-row"}) if e.page == 0]
    assert len(rows) == 3
    assert len(r.html.split("outlook-row")) - 1 == 3     # five days of data, three on the front page


def test_a_missing_detail_prints_na_and_the_band_still_fits(base):
    inp = dataclasses.replace(expanded_input(base), weather=expanded_weather(
        visibility=None, pressure=None, humidity=None, precip_24h=None, uv_index_max=None, uv_category=""))
    r = renderer.render_edition(inp)
    doc = assert_fold_clear(r.html)
    assert r.html.count("n/a") == 5 and "7:12 am / 6:08 pm" in r.html and "42, Good" in r.html
    assert any(e.page == 1 for e in extents(doc, {"weather-band"}))


# ---- weather icons and moon ----

def sections(html):
    """The rendered HTML cut into the front-page ear, the outlook rows, and the page-2 band."""
    ear = html.split('class="ear"', 1)[1].split('class="nameplate"', 1)[0]
    band = html.split('class="region weather-band"', 1)[1]
    band = band.split('class="region', 1)[0]
    return ear, band


def test_icons_appear_in_the_ear_outlook_rows_and_band(base):
    r = renderer.render_edition(expanded_input(base))
    ear, band = sections(r.html)
    assert ear.count("<svg") == 1
    rows = r.html.split('class="outlook-row"')[1:]
    assert len(rows) == 3 and all(row.split("</div>", 1)[0].count("<svg") == 1 for row in rows)
    days = band.split('class="wx-days"')[1]
    assert days.count("<svg") == 5
    grid = band.split('class="wx-days"')[0]
    assert grid.count("<svg") == 1 and "wx-moon" in grid
    assert r.html.count("<svg") == 1 + 3 + 5 + 1
    assert "&lt;svg" not in r.html


def test_icon_size_and_nowrap_temps_in_the_band(base):
    r = renderer.render_edition(expanded_input(base))
    assert r.html.count("width:0.3in;height:0.3in") == 1          # the ear
    assert r.html.count("width:0.4in;height:0.4in") == 5          # the forecast day cells
    assert r.html.count("width:0.17in;height:0.17in") == 3
    assert r.html.count("width:0.42in;height:0.42in") == 1
    assert ".wx-temps { white-space: nowrap; }" in (renderer.TEMPLATES / "edition.css").read_text()


def test_icons_show_the_kind_for_each_days_weather_code(base):
    from morning_edition.layout import icons
    r = renderer.render_edition(expanded_input(base))
    days = r.html.split('class="wx-days"')[1]
    for kind in ("rain", "clear", "cloudy", "storm"):     # codes 81, 0, 3, 95 (and 61 for rain again)
        assert icons.icon(kind, "0.4in") in days
    assert icons.icon("partly", "0.3in") in sections(r.html)[0]     # today is code 2


def test_moon_in_the_band_follows_waxing(base):
    from morning_edition.layout import icons
    waxing = renderer.render_edition(dataclasses.replace(
        expanded_input(base), weather=expanded_weather(moon_waxing=True))).html
    waning = renderer.render_edition(dataclasses.replace(
        expanded_input(base), weather=expanded_weather(moon_waxing=False, moon_phase="Waning Crescent"))).html
    assert icons.moon(0.23, True, "0.42in") in waxing and icons.moon(0.23, False, "0.42in") in waning
    assert icons.moon(0.23, True, "0.42in") not in waning


def test_icons_stay_above_the_fold_and_inside_the_band(base):
    r = renderer.render_edition(expanded_input(base))
    doc = assert_fold_clear(r.html)
    band = next(e for e in extents(doc, {"weather-band"}))
    assert band.bottom <= g.INSIDE_Y + g.WEATHER_BAND_H + 0.01
    assert all(e.bottom <= band.bottom + 0.01 for e in extents(doc, {"wx-day", "wx-cell"}) if e.page == 1)
    rows = [e for e in extents(doc, {"outlook-row", "cal-row"}) if e.page == 0]
    assert rows and all(e.bottom <= g.FOLD for e in rows)
    ears = [e for e in extents(doc, {"ear"}) if e.page == 0]
    assert ears and all(e.bottom <= g.MARGIN_Y + g.MASTHEAD_H for e in ears)


def test_long_summaries_do_not_break_the_sidebar_or_band(base):
    wx = expanded_weather().model_copy(update={"outlook": [
        forecast(13, "Thunderstorms, hail", 96), forecast(14, "Heavy snow showers", 86),
        forecast(15, "Freezing drizzle", 57), forecast(16, "Heavy showers", 82),
        forecast(17, "Thunderstorms, hail", 99)]})
    r = renderer.render_edition(dataclasses.replace(expanded_input(base), weather=wx))
    doc = assert_fold_clear(r.html)
    band = next(e for e in extents(doc, {"weather-band"}))
    assert all(e.bottom <= band.bottom + 0.01 for e in extents(doc, {"wx-day"}) if e.page == 1)
    days = sorted((e for e in extents(doc, {"wx-day"}) if e.page == 1), key=lambda e: e.left)
    pops = sorted((e for e in extents(doc, {"wx-pop"}) if e.page == 1), key=lambda e: e.left)
    assert all(p.bottom <= d.bottom + 0.005 for p, d in zip(pops, days)), "a day's text runs out of its cell"
    rows = [e for e in extents(doc, {"outlook-row", "cal-row"}) if e.page == 0]
    assert all(e.bottom <= g.FOLD for e in rows)


def test_plain_weather_without_the_band_still_has_the_ear_and_outlook_icons(base):
    wx = Weather(current_temp=58, today=forecast(12), wind="Light NW wind",
                 outlook=[forecast(13, "Showers", 80), forecast(14, "Sunny", 0), forecast(15, "Cloudy", 3)])
    r = renderer.render_edition(dataclasses.replace(base, weather=wx))
    assert r.html.count("<svg") == 4 and "weather-band" not in r.html.split("</style>", 1)[1]
    assert dataclasses.replace(base).weather is None
    assert "<svg" not in renderer.render_edition(base).html      # no weather, no icons


def test_quiet_day_with_weather_icons_still_renders(base):
    quiet = dataclasses.replace(expanded_input(base), lead=None, good_news=None, primary=[], reserves=[])
    r = renderer.render_edition(quiet)
    assert_fold_clear(r.html)
    assert r.html.count("<svg") == 10 and r.pages == 4


# ---- nameplate and ear fitting ----

LONG_NAME = "The Greater Example Morning Chronicle"


def masthead_input(base, font, name, weather=None):
    settings = base.settings.model_copy(update={"masthead_font": font, "paper_name": name})
    return dataclasses.replace(base, settings=settings, weather=weather or expanded_weather(), lead=None,
                               good_news=None, primary=[], reserves=[])


def nameplate_size(html):
    return int(re.search(r'class="nameplate" style="font-size: (\d+)pt', html).group(1))


@pytest.mark.parametrize("font", ["Pirata One", "Manufacturing Consent", "Grenze Gotisch", "Playfair Display"])
@pytest.mark.parametrize("name", ["Morning Edition", "The Rick Times", LONG_NAME])
def test_nameplate_is_one_line_inside_the_masthead_in_every_font(base, font, name):
    r = renderer.render_edition(masthead_input(base, font, name))
    doc = render_document(r.html)
    (plate,) = [e for e in extents(doc, {"nameplate"}) if e.page == 0]
    size = nameplate_size(r.html)
    assert plate.bottom - plate.top <= size / 72 * 1.05, f"{font} {name} wrapped at {size}pt"
    (mast,) = [e for e in extents(doc, {"masthead"}) if e.page == 0]
    assert mast.bottom - mast.top <= 1.0 + 0.01 and mast.top <= plate.top and plate.bottom <= mast.bottom
    ears = sorted((e for e in extents(doc, {"ear"}) if e.page == 0), key=lambda e: e.left)
    assert plate.left >= ears[0].right - 0.01 and plate.right <= ears[1].left + 0.01
    assert all(abs((e.right - e.left) - g.EAR_W) < 0.01 for e in ears)


def test_a_very_long_name_steps_the_nameplate_down(base):
    short = renderer.render_edition(masthead_input(base, "Pirata One", "The Rick Times"))
    long = renderer.render_edition(masthead_input(base, "Pirata One", LONG_NAME))
    assert nameplate_size(short.html) == 60
    assert nameplate_size(long.html) < 60
    assert LONG_NAME in long.html


def test_test_page_nameplate_steps_down_too():
    settings = Settings(paper_name=LONG_NAME, masthead_font="Playfair Display")
    assert renderer._nameplate_size(settings, g.CONTENT_W - 2 * g.NAMEPLATE_GAP) < 60
    assert renderer.render_test_page(settings, "Oct 12, 2026 9:00 am").startswith(b"%PDF")


def test_ear_width_in_the_css_matches_the_geometry():
    css = (renderer.TEMPLATES / "edition.css").read_text()
    assert f".ear {{ width: {g.EAR_W}in;" in css


@pytest.mark.parametrize("units,deg", [("us", "&deg;"), ("metric", "°C")])
def test_extreme_temperatures_stay_on_one_line_in_the_ear(base, units, deg):
    today = DayForecast(day=date(2026, 10, 12), high=104, low=-12, summary="Hot", precip_chance=0, code=0)
    wx = Weather(current_temp=100, today=today, wind="Calm", units=units, outlook=[])
    r = renderer.render_edition(dataclasses.replace(base, weather=wx))
    assert f"104{deg} / -12{deg}" in r.html
    css = (renderer.TEMPLATES / "edition.css").read_text()
    assert re.search(r"\.ear \.big \{ white-space: nowrap;", css)
    size = int(re.search(r'class="big" style="font-size: (\d+)pt', r.html).group(1))
    assert size < renderer.EAR_TEMP_SIZES[0]                 # too wide at the normal size, so it stepped down
    doc = render_document(r.html)
    big = next(e for e in extents(doc, {"big"}) if e.page == 0)
    assert big.bottom - big.top <= size / 72 * 1.05 * 1.05   # a single line
    inside = g.EAR_W - 2 * (g.EAR_PAD_X + g.EAR_BORDER) - renderer.EAR_ICON_W
    mark = Markup("&deg;") if units == "us" else chr(0xB0) + "C"
    text = Markup("{}{} / {}{}").format(104, mark, -12, mark)
    probe = lambda sz: widths(renderer._env.get_template("measure.html.j2").render(  # noqa: E731
        items=[("p", 8.0, "text_probe", {"key": "w", "text": text, "font": "Libre Franklin", "weight": 700, "size": sz})],
        **renderer._common()))["w"]
    assert probe(size) <= inside < probe(size + 1)


def test_a_normal_temperature_keeps_the_full_ear_size(base):
    r = renderer.render_edition(dataclasses.replace(base, weather=expanded_weather()))
    assert 'class="big" style="font-size: 15pt"' in r.html


def test_ears_sit_inside_the_masthead_above_its_rule(base):
    settings = base.settings.model_copy(update={"weather_locations": [WeatherLocation(label="New York, New York")]})
    inp = dataclasses.replace(base, settings=settings, weather=expanded_weather(), lead=None, good_news=None,
                              primary=[], reserves=[])
    doc = render_document(renderer.render_edition(inp).html)
    (mast,) = [e for e in extents(doc, {"masthead"}) if e.page == 0]
    rule = 2.5 / 72
    for ear in (e for e in extents(doc, {"ear"}) if e.page == 0):
        assert mast.top - 0.01 <= ear.top and ear.bottom <= mast.bottom - rule - 0.02


def test_forecast_summary_is_on_its_own_line_above_the_icon(base):
    doc = render_document(renderer.render_edition(expanded_input(base)).html)
    cells = {c: sorted((e for e in extents(doc, {c}) if e.page == 1), key=lambda e: e.left)
             for c in ("wx-day", "wx-summary", "wx-icon", "wx-temps", "wx-pop")}
    assert all(len(v) == 5 for v in cells.values())
    for i, icon_box in enumerate(cells["wx-icon"]):
        assert abs((icon_box.right - icon_box.left) - 0.4) < 0.01
        for cls in ("wx-temps", "wx-pop"):
            assert cells[cls][i].left >= icon_box.right - 0.01, f"day {i} {cls} sits under the icon"
        summary, day = cells["wx-summary"][i], cells["wx-day"][i]
        assert summary.bottom <= icon_box.top + 0.01, f"day {i} summary is not above the icon"
        assert summary.right - summary.left > 1.0                          # the whole cell width, not a narrow column
        assert day.left - 0.01 <= icon_box.left and cells["wx-pop"][i].right <= day.right + 0.01


def test_thunderstorms_fits_on_one_line_in_its_forecast_cell(base):
    doc = render_document(renderer.render_edition(expanded_input(base)).html)   # Sat is "Thunderstorms"
    summaries = [e for e in extents(doc, {"wx-summary"}) if e.page == 1]
    (storm,) = [e for e in summaries if e.text == "Thunderstorms"]
    assert storm.bottom - storm.top <= 11 / 72 + 0.01                   # one 11pt line
    assert all(e.bottom - e.top <= 11 / 72 + 0.01 for e in summaries)


def test_band_details_are_in_the_approved_order(base):
    doc = render_document(renderer.render_edition(expanded_input(base)).html)
    cells = sorted((e for e in extents(doc, {"wx-cell"}) if e.page == 1), key=lambda e: (round(e.top, 2), e.left))
    assert len(cells) == 8
    rows = [cells[:4], cells[4:]]
    expected = [["Air quality", "Pressure", "Humidity", "Sunrise / Sunset"],
                ["UV index max", "Visibility", "Rain past 24 h", "Moon phase"]]
    for row, labels in zip(rows, expected):
        assert all(c.text.startswith(label) for c, label in zip(row, labels)), [c.text for c in row]
        assert len({round(c.top, 2) for c in row}) == 1                      # a row shares one top
        assert [c.left for c in row] == sorted(c.left for c in row) and len({c.left for c in row}) == 4
    assert rows[1][0].top > rows[0][0].top
    assert [round(c.left, 2) for c in rows[0]] == [round(c.left, 2) for c in rows[1]]   # same four columns
    band = next(e for e in extents(doc, {"weather-band"}))
    assert all(c.bottom <= band.bottom + 0.01 for c in cells)
    (days,) = [e for e in extents(doc, {"wx-days"}) if e.page == 1]
    (moon_text,) = [e for e in extents(doc, {"wx-moon-text"}) if e.page == 1]
    assert moon_text.bottom <= days.top + 0.005, "the moon text runs into the forecast rule"


BOTTOM_CLASSES = {"ragged", "cols", "brief", "story-header", "vrule"}


def strip_input(base, **over):
    wx = Weather(current_temp=58, today=forecast(12), wind="Wind NW 8 mph",
                 outlook=[forecast(13, "Showers"), forecast(14, "Sunny"), forecast(15, "Cloudy")])
    events = [Event(label=f"{9 + i}:00 am", title=f"Calendar entry number {i}", all_day=False, sort_minute=540 + i)
              for i in range(5)]
    return dataclasses.replace(base, weather=wx, events=events, **over)


def test_index_strip_sits_below_every_bottom_region_on_page_one(base):
    r = renderer.render_edition(strip_input(base))
    doc = assert_fold_clear(r.html)
    (strip,) = [e for e in extents(doc, {"index-strip"}) if e.page == 0]
    assert abs(strip.top - g.INDEX_STRIP_Y) < 0.01 and abs((strip.bottom - strip.top) - g.INDEX_STRIP_H) < 0.01
    bottom = [e for e in extents(doc, BOTTOM_CLASSES) if e.page == 0 and e.top > g.FOLD]
    assert {e.cls for e in bottom} >= {"ragged", "cols", "brief", "story-header"}
    assert all(e.bottom <= strip.top + 0.001 for e in bottom)
    regions = [e for e in bottom if e.cls in {"ragged", "vrule"}]
    assert regions and all(e.bottom <= g.FRONT_BOTTOM_Y + g.front_bottom_h(True) + 0.001 for e in regions)
    assert strip.bottom <= g.PAGE_H - g.MARGIN_Y + 0.001


def test_index_strip_lists_every_entry_in_order_on_one_line(base):
    r = renderer.render_edition(strip_input(base))
    doc = render_document(r.html)
    (line,) = [e for e in extents(doc, {"index-line"}) if e.page == 0]
    assert line.bottom - line.top <= 12 / 72 + 0.001      # one line of 10pt leading, never wrapped
    items = [e for e in extents(doc, {"idx-item", "idx-label"}) if e.page == 0]
    assert all(e.right <= g.MARGIN_X + g.CONTENT_W + 0.001 for e in items)
    assert len({round(e.top, 2) for e in items}) == 1
    assert [e.text.upper() for e in items][0] == "INSIDE"
    names = [" ".join(e.text.split()[:-1]) for e in items[1:]]
    assert names == SECTIONS + ["Good News", "Sudoku"]
    assert "<b>Good News</b> 6" in r.html and "<b>Sudoku</b> 6" in r.html
    for name in SECTIONS:
        assert f"<b>{name}</b> " in r.html


def test_sidebar_no_longer_holds_the_index(base):
    r = renderer.render_edition(strip_input(base))
    side = r.html[r.html.index('class="region side"'):r.html.index('class="region rule-top"')]
    assert "Inside" not in side and "index-row" not in side
    assert "Your Day" in side and "Outlook" in side


def test_five_events_and_three_day_outlook_both_fit_on_page_one(base):
    r = renderer.render_edition(strip_input(base))
    doc = render_document(r.html)
    assert len([e for e in extents(doc, {"cal-row"}) if e.page == 0]) == 5
    rows = [e for e in extents(doc, {"outlook-row"}) if e.page == 0]
    assert len(rows) == 3 and all(e.bottom <= g.FOLD for e in rows)


def test_a_long_index_steps_the_strip_font_down_and_stays_one_line(base):
    names = ["Neighborhood Affairs", "International Affairs", "Science and Tech", "Culture"]
    primary = [piece(i, "story", names[i % 4], n=3) for i in range(3, 18)]
    settings = base.settings.model_copy(update={"sections": names, "target_sheets": 3, "max_sheets": 3})
    r = renderer.render_edition(strip_input(base, settings=settings, primary=primary))
    doc = render_document(r.html)
    (line,) = [e for e in extents(doc, {"index-line"}) if e.page == 0]
    assert line.bottom - line.top <= 12 / 72 + 0.001
    size = float(re.search(r'class="index-line" style="font-size: ([\d.]+)pt"', r.html).group(1))
    assert size in renderer.INDEX_STRIP_SIZES and size < renderer.INDEX_STRIP_SIZES[0]
    items = [e for e in extents(doc, {"idx-item"}) if e.page == 0]
    assert len(items) >= 5 and all(e.right <= g.MARGIN_X + g.CONTENT_W + 0.001 for e in items)


def test_a_short_index_keeps_the_full_strip_font(base):
    r = renderer.render_edition(strip_input(base))
    assert 'class="index-line" style="font-size: 7.5pt"' in r.html


def test_no_index_means_no_strip_and_full_height_bottom_regions(base):
    quiet = dataclasses.replace(base, good_news=None, primary=[], reserves=[], puzzle=None, lead=piece(1, "lead", n=14))
    settings = base.settings.model_copy(update={"target_sheets": 1, "max_sheets": 1})
    r = renderer.render_edition(dataclasses.replace(quiet, settings=settings))
    doc = render_document(r.html)
    assert "index-strip" not in r.html.split("</style>")[1]
    assert not extents(doc, {"index-strip"})
    (cont,) = [e for e in extents(doc, {"ragged"}) if e.page == 0 and e.top > g.FOLD]
    assert abs(cont.bottom - (g.PAGE_H - g.MARGIN_Y)) < 0.01
