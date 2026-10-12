import json
import random
import re
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

import httpx
import pytest
import respx
from PIL import Image

from morning_edition import db, pipeline
from morning_edition.llm import editor
from morning_edition.llm.client import LLMError
from morning_edition.printing import PrintError
from morning_edition.settings import Settings, WeatherLocation

NOW = datetime(2026, 10, 12, 9, 10, tzinfo=timezone.utc)
ARTICLE = (Path(__file__).parent / "fixtures" / "article.html").read_text()


def rss(prefix, n, section_title):
    items = "".join(
        f"<item><title>{section_title} story {i}</title><link>https://news.example.com/{prefix}/{i}</link>"
        f"<description>Teaser {i}.</description><pubDate>Mon, 12 Oct 2026 0{i % 9}:00:00 GMT</pubDate></item>"
        for i in range(n))
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>{section_title} Wire</title>{items}</channel></rss>'


def jpeg():
    buf = BytesIO()
    Image.new("RGB", (1200, 800), (128, 128, 128)).save(buf, "JPEG")
    return buf.getvalue()


class FakeLLM:
    """Answers each schema the editor asks for."""

    def ask_json(self, system, user, schema, max_tokens=16000):
        if schema is editor.Selection:
            ids = [int(x) for x in re.findall(r'"id": (\d+)', user)]
            return editor.Selection(lead_id=ids[0], good_news_id=None,
                                    primary=[editor.Pick(id=i, length="story") for i in ids[1:6]],
                                    reserves=[editor.Pick(id=i, length="brief") for i in ids[6:9]], also=[])
        if schema is editor.Condensed:
            return editor.Condensed(headline="A Condensed Headline", deck="A deck.",
                                    paragraphs=["The council voted to rebuild the seawall. " * 3] * 4)
        if schema is editor.ClueSet:
            entries = re.findall(r"^(\d+) (Across|Down): ", user, re.M)
            return editor.ClueSet(clues=[editor.Clue(number=int(n), direction=d.lower(), clue="A clue")
                                         for n, d in entries])
        raise AssertionError(schema)


class FakePrinter:
    def __init__(self, fail=False):
        self.fail, self.printed = fail, []

    def ensure_queue(self, address):
        if self.fail:
            raise PrintError("printer offline")

    def print_pdf(self, path):
        self.printed.append(path)
        return "morning-edition-1"


@pytest.fixture
def setup(tmp_path):
    db_path = tmp_path / "me.sqlite"
    conn = db.connect(db_path)
    db.save_settings(conn, Settings(paper_name="The Test Times", timezone="America/New_York",
                                    weather_locations=[WeatherLocation(label="Testville", latitude=40.75,
                                                                       longitude=-73.99)],
                                    printer_address="10.0.0.9", puzzle_mode="sudoku"))
    db.add_feed(conn, "https://feeds.example.com/local", "Local")
    db.add_feed(conn, "https://feeds.example.com/world", "World")
    conn.close()
    return db_path, tmp_path / "data"


def services(printer=None, llm=True):
    return pipeline.Services(http=httpx.Client(), llm_factory=lambda s: FakeLLM() if llm else None,
                             printer=printer or FakePrinter(), now=lambda: NOW, rng=random.Random(5))


def mock_world(respx_mock, feeds_alive=True):
    if feeds_alive:
        respx_mock.get("https://feeds.example.com/local").mock(return_value=httpx.Response(200, text=rss("l", 8, "Local")))
        respx_mock.get("https://feeds.example.com/world").mock(return_value=httpx.Response(200, text=rss("w", 8, "World")))
    else:
        respx_mock.get("https://feeds.example.com/local").mock(side_effect=httpx.ConnectTimeout("down"))
        respx_mock.get("https://feeds.example.com/world").mock(return_value=httpx.Response(500))
    respx_mock.get(url__regex=r"https://news\.example\.com/[lw]/\d+").mock(return_value=httpx.Response(200, html=ARTICLE))
    respx_mock.get("https://news.example.com/lead.jpg").mock(return_value=httpx.Response(200, content=jpeg()))
    respx_mock.get(host="api.open-meteo.com").mock(return_value=httpx.Response(200, json={
        "current": {"time": "2026-10-12T05:00", "temperature_2m": 55.0},
        "daily": {"time": ["2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15"],
                  "weather_code": [0, 2, 61, 3], "temperature_2m_max": [68, 69, 61, 64],
                  "temperature_2m_min": [51, 52, 50, 47], "precipitation_probability_max": [0, 10, 80, 20],
                  "wind_speed_10m_max": [9, 13, 15, 8], "wind_direction_10m_dominant": [315, 180, 90, 0]}}))
    respx_mock.get(host="api.wikimedia.org").mock(return_value=httpx.Response(200, json={"selected": [
        {"year": 1492, "text": "Columbus reached the Bahamas."}]}))


@respx.mock
def test_full_build_prints_and_records(setup):
    mock_world(respx.mock)
    db_path, data_dir = setup
    printer = FakePrinter()
    eid = pipeline.build_edition(db_path, data_dir, services(printer), print_it=True)
    conn = db.connect(db_path)
    ed = db.get_edition(conn, eid)
    assert ed.status == "printed"
    assert Path(ed.pdf_path).read_bytes().startswith(b"%PDF")
    assert printer.printed == [Path(ed.pdf_path)]
    steps = {e["step"]: e["status"] for e in ed.log}
    assert steps["gather"] == "ok" and steps["layout"] in ("ok", "warn") and steps["print"] == "ok"
    assert ed.puzzle_type == "sudoku" and json.loads(ed.puzzle_solution)["kind"] == "sudoku"
    assert len(db.seen_keys(conn)) >= 2
    assert (data_dir / "editions" / "0001" / "lead-halftone.png").exists()


@respx.mock
def test_second_edition_skips_seen_stories_and_shows_answers(setup):
    mock_world(respx.mock)
    db_path, data_dir = setup
    pipeline.build_edition(db_path, data_dir, services(), print_it=False)
    eid = pipeline.build_edition(db_path, data_dir, services(), print_it=False)
    conn = db.connect(db_path)
    ed = db.get_edition(conn, eid)
    assert ed.number == 2 and ed.status == "built"
    html = (data_dir / "editions" / "0002" / "edition.html").read_text()
    assert "Yesterday" in html


@respx.mock
def test_dead_feeds_still_print_a_quiet_paper(setup):
    mock_world(respx.mock, feeds_alive=False)
    db_path, data_dir = setup
    eid = pipeline.build_edition(db_path, data_dir, services(), print_it=True)
    ed = db.get_edition(db.connect(db_path), eid)
    assert ed.status == "printed"
    gather = next(e for e in ed.log if e["step"] == "gather")
    assert gather["status"] == "warn" and "feeds.example.com" in gather["message"]
    html = (data_dir / "editions" / "0001" / "edition.html").read_text()
    assert "A Quiet News Day" in html and "Columbus" in html


@respx.mock
def test_print_failure_keeps_pdf(setup):
    mock_world(respx.mock)
    db_path, data_dir = setup
    eid = pipeline.build_edition(db_path, data_dir, services(FakePrinter(fail=True)), print_it=True)
    ed = db.get_edition(db.connect(db_path), eid)
    assert ed.status == "print_failed" and Path(ed.pdf_path).exists()
    printer = FakePrinter()
    pipeline.reprint(db_path, eid, services(printer))
    assert db.get_edition(db.connect(db_path), eid).status == "printed"
    assert printer.printed


@respx.mock
def test_no_api_key_uses_fallbacks(setup):
    mock_world(respx.mock)
    db_path, data_dir = setup
    eid = pipeline.build_edition(db_path, data_dir, services(llm=False), print_it=False)
    assert db.get_edition(db.connect(db_path), eid).status == "built"


def test_puzzle_kind():
    assert pipeline.puzzle_kind("alternate", 2) == "sudoku"
    assert pipeline.puzzle_kind("alternate", 3) == "crossword"
    assert pipeline.puzzle_kind("sudoku", 3) == "sudoku"
    assert pipeline.puzzle_kind("crossword", 2) == "crossword"


def test_build_lock(setup):
    db_path, data_dir = setup
    with pipeline.BUILD_LOCK:
        with pytest.raises(RuntimeError):
            pipeline.build_edition(db_path, data_dir, services(), print_it=False)


def ics(uid, title, hhmm="090000"):
    return ("BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//test//EN\nBEGIN:VEVENT\n"
            f"UID:{uid}\nDTSTART;TZID=America/New_York:20261012T{hhmm}\n"
            f"DTEND;TZID=America/New_York:20261012T{int(hhmm) + 10000:06d}\nSUMMARY:{title}\n"
            "END:VEVENT\nEND:VCALENDAR\n")


def set_calendars(db_path, *pairs):
    from morning_edition.settings import CalendarLink
    conn = db.connect(db_path)
    links = [CalendarLink(name=n, url=u) for n, u in pairs]
    db.save_settings(conn, db.load_settings(conn).model_copy(update={"calendars": links}))
    conn.close()


@respx.mock
def test_calendar_failure_never_logs_the_private_url(setup):
    mock_world(respx.mock)
    db_path, data_dir = setup
    secret = "https://cal.example.com/private/SECRETTOKEN/basic.ics"
    set_calendars(db_path, ("Work", secret))
    respx.mock.get(secret).mock(side_effect=httpx.ConnectTimeout(f"timed out for {secret}"))
    eid = pipeline.build_edition(db_path, data_dir, services(), print_it=False)
    ed = db.get_edition(db.connect(db_path), eid)
    day = next(e for e in ed.log if e["step"] == "day")
    assert day["status"] == "warn" and 'calendar "Work": could not load' in day["message"]
    assert "SECRETTOKEN" not in json.dumps(ed.log)


@respx.mock
def test_expanded_weather_setting_reaches_the_forecast_request(setup):
    mock_world(respx.mock)
    db_path, data_dir = setup
    conn = db.connect(db_path)
    db.save_settings(conn, db.load_settings(conn).model_copy(update={"expanded_weather": True}))
    conn.close()
    aqi = respx.mock.get(host="air-quality-api.open-meteo.com").mock(
        return_value=httpx.Response(200, json={"current": {"us_aqi": 42}}))
    pipeline.build_edition(db_path, data_dir, services(), print_it=False)
    forecast = next(c for c in respx.calls if c.request.url.path == "/v1/forecast")
    assert forecast.request.url.params["past_days"] == "1"
    assert aqi.called


@respx.mock
def test_home_location_and_metric_units_reach_the_forecast_request(setup):
    mock_world(respx.mock)
    db_path, data_dir = setup
    conn = db.connect(db_path)
    db.save_settings(conn, db.load_settings(conn).model_copy(update={"weather_units": "metric"}))
    conn.close()
    pipeline.build_edition(db_path, data_dir, services(), print_it=False)
    forecast = next(c for c in respx.calls if c.request.url.path == "/v1/forecast")
    p = forecast.request.url.params
    assert (p["latitude"], p["longitude"]) == ("40.75", "-73.99")
    assert (p["temperature_unit"], p["wind_speed_unit"], p["precipitation_unit"]) == ("celsius", "kmh", "mm")


@respx.mock
def test_no_home_coordinates_means_no_weather_request(setup):
    mock_world(respx.mock)
    db_path, data_dir = setup
    conn = db.connect(db_path)
    db.save_settings(conn, db.load_settings(conn).model_copy(update={"weather_locations": []}))
    conn.close()
    pipeline.build_edition(db_path, data_dir, services(), print_it=False)
    assert not [c for c in respx.calls if c.request.url.path == "/v1/forecast"]


@respx.mock
def test_plain_weather_by_default(setup):
    mock_world(respx.mock)
    db_path, data_dir = setup
    aqi = respx.mock.get(host="air-quality-api.open-meteo.com").mock(return_value=httpx.Response(200, json={}))
    pipeline.build_edition(db_path, data_dir, services(), print_it=False)
    forecast = next(c for c in respx.calls if c.request.url.path == "/v1/forecast")
    assert "past_days" not in forecast.request.url.params and not aqi.called


@respx.mock
def test_one_failing_calendar_does_not_lose_the_others(setup):
    mock_world(respx.mock)
    db_path, data_dir = setup
    good_a = "https://cal.example.com/a/GOODTOKENA/basic.ics"
    bad = "https://cal.example.com/b/BADTOKEN/basic.ics"
    good_c = "webcal://cal.example.com/c/GOODTOKENC/basic.ics"
    set_calendars(db_path, ("Work", good_a), ("Family", bad), ("Boat", good_c))
    respx.mock.get(good_a).mock(return_value=httpx.Response(200, text=ics("1", "Zebra meeting", "130000")))
    respx.mock.get(bad).mock(return_value=httpx.Response(500, text=f"boom {bad}"))
    respx.mock.get("https://cal.example.com/c/GOODTOKENC/basic.ics").mock(
        return_value=httpx.Response(200, text=ics("2", "Alpha breakfast", "080000")))
    eid = pipeline.build_edition(db_path, data_dir, services(), print_it=False)
    ed = db.get_edition(db.connect(db_path), eid)
    day = next(e for e in ed.log if e["step"] == "day")
    assert day["status"] == "warn" and "2 events" in day["message"]
    assert 'calendar "Family": could not load' in day["message"]
    assert 'calendar "Work"' not in day["message"] and 'calendar "Boat"' not in day["message"]
    html = (data_dir / "editions" / "0001" / "edition.html").read_text()
    assert html.index("Alpha breakfast") < html.index("Zebra meeting")   # merged and sorted by time
    dumped = json.dumps(ed.log) + html
    assert "BADTOKEN" not in dumped and "GOODTOKEN" not in dumped and "cal.example.com" not in dumped


@respx.mock
def test_calendar_failure_leaves_weather_and_on_this_day_alone(setup):
    mock_world(respx.mock)
    db_path, data_dir = setup
    url = "https://cal.example.com/x/basic.ics"
    set_calendars(db_path, ("Work", url))
    respx.mock.get(url).mock(return_value=httpx.Response(404))
    eid = pipeline.build_edition(db_path, data_dir, services(), print_it=False)
    day = next(e for e in db.get_edition(db.connect(db_path), eid).log if e["step"] == "day")
    assert "weather ok" in day["message"] and "1 on-this-day items" in day["message"]


def test_unexpected_error_never_strands_an_edition_in_building(setup):
    db_path, data_dir = setup

    def boom(settings):
        raise RuntimeError("factory exploded")

    svc = pipeline.Services(http=httpx.Client(), llm_factory=boom, printer=FakePrinter(),
                            now=lambda: NOW, rng=random.Random(5))
    eid = pipeline.build_edition(db_path, data_dir, svc, print_it=False)
    ed = db.get_edition(db.connect(db_path), eid)
    assert ed.status == "failed"
    entry = next(e for e in ed.log if e["step"] == "build")
    assert entry["status"] == "failed" and "RuntimeError: factory exploded" in entry["message"]


@respx.mock
def test_failure_after_pdf_written_marks_edition_failed(setup, monkeypatch):
    mock_world(respx.mock)
    db_path, data_dir = setup

    def boom(*args, **kwargs):
        raise RuntimeError("db hiccup")

    monkeypatch.setattr(db, "record_stories", boom)
    eid = pipeline.build_edition(db_path, data_dir, services(), print_it=True)
    ed = db.get_edition(db.connect(db_path), eid)
    assert ed.status == "failed"
    assert any(e["step"] == "layout" and e["status"] == "failed" for e in ed.log)


@respx.mock
def test_one_bad_article_does_not_drop_the_other_pieces(setup, monkeypatch):
    mock_world(respx.mock)
    db_path, data_dir = setup
    real = editor.condense
    bad = {"title": None}

    def flaky(llm, item, text, kind):
        if bad["title"] is None:
            bad["title"] = item.title
        if item.title == bad["title"]:
            raise ValueError("https://secret.example.com/token boom")
        return real(llm, item, text, kind)

    monkeypatch.setattr(editor, "condense", flaky)
    eid = pipeline.build_edition(db_path, data_dir, services(), print_it=False)
    ed = db.get_edition(db.connect(db_path), eid)
    assert ed.status == "built"
    pieces = json.loads((data_dir / "editions" / "0001" / "pieces.json").read_text())
    assert len(pieces) >= 3 and all(p["headline"] for p in pieces)
    write = next(e for e in ed.log if e["step"] == "write")
    assert write["status"] == "warn" and "skipped" in write["message"] and "secret.example.com" not in write["message"]


@respx.mock
def test_unexpected_print_error_marks_print_failed(setup):
    mock_world(respx.mock)
    db_path, data_dir = setup

    class Crashy(FakePrinter):
        def print_pdf(self, path):
            raise OSError("spooler gone")

    eid = pipeline.build_edition(db_path, data_dir, services(Crashy()), print_it=True)
    ed = db.get_edition(db.connect(db_path), eid)
    assert ed.status == "print_failed" and Path(ed.pdf_path).exists()


def _log(db_path, eid):
    return {e["step"]: e for e in db.get_edition(db.connect(db_path), eid).log}


@respx.mock
def test_claude_failures_are_visible_in_the_edition_log(setup):
    from types import SimpleNamespace

    from morning_edition.llm.client import LLM

    class Broken:
        def create(self, **kwargs):
            raise RuntimeError("upstream is down")

    mock_world(respx.mock)
    db_path, data_dir = setup
    llm = LLM("m", "low", client=SimpleNamespace(beta=SimpleNamespace(messages=Broken())))
    svc = pipeline.Services(http=httpx.Client(), llm_factory=lambda s: llm, printer=FakePrinter(),
                            now=lambda: NOW, rng=random.Random(5))
    eid = pipeline.build_edition(db_path, data_dir, svc, print_it=False)
    steps = _log(db_path, eid)
    assert steps["select"]["status"] == "warn"
    assert "Claude failed 1 time; first error: " in steps["select"]["message"]
    assert "upstream is down" in steps["select"]["message"]
    assert db.get_edition(db.connect(db_path), eid).status == "built"  # the paper still prints


@respx.mock
def test_healthy_claude_adds_no_warning(setup):
    mock_world(respx.mock)
    db_path, data_dir = setup
    eid = pipeline.build_edition(db_path, data_dir, services(), print_it=False)
    assert "Claude failed" not in json.dumps(db.get_edition(db.connect(db_path), eid).log)


@respx.mock
def test_write_step_reports_claude_failures(setup):
    mock_world(respx.mock)
    db_path, data_dir = setup

    class CondenseBroken(FakeLLM):
        errors: list = []

        def ask_json(self, system, user, schema, max_tokens=16000):
            if schema is editor.Condensed:
                self.errors.append("RuntimeError: nope")
                raise LLMError("nope")
            return super().ask_json(system, user, schema, max_tokens)

    broken = CondenseBroken()
    broken.errors = []
    svc = pipeline.Services(http=httpx.Client(), llm_factory=lambda s: broken, printer=FakePrinter(),
                            now=lambda: NOW, rng=random.Random(5))
    eid = pipeline.build_edition(db_path, data_dir, svc, print_it=False)
    msg = _log(db_path, eid)["write"]["message"]
    assert "Claude failed" in msg and "times; first error: RuntimeError: nope" in msg


@respx.mock
def test_crossword_branch_falls_back_to_sudoku_on_any_exception(setup, monkeypatch):
    mock_world(respx.mock)
    db_path, data_dir = setup
    conn = db.connect(db_path)
    db.save_settings(conn, db.load_settings(conn).model_copy(update={"puzzle_mode": "crossword"}))
    conn.close()

    def boom(llm, entries):
        raise TypeError("not an LLMError")

    monkeypatch.setattr(editor, "write_clues", boom)
    eid = pipeline.build_edition(db_path, data_dir, services(), print_it=False)
    ed = db.get_edition(db.connect(db_path), eid)
    assert ed.status == "built" and ed.puzzle_type == "sudoku"
    puzzle = _log(db_path, eid)["puzzle"]
    assert puzzle["status"] == "warn" and "using sudoku" in puzzle["message"]


@respx.mock
def test_weather_crash_does_not_drop_on_this_day(setup, monkeypatch):
    mock_world(respx.mock)
    db_path, data_dir = setup

    def boom(*args, **kwargs):
        raise TypeError("weather parser bug")

    monkeypatch.setattr(pipeline.weather, "fetch_weather", boom)
    eid = pipeline.build_edition(db_path, data_dir, services(), print_it=False)
    day = _log(db_path, eid)["day"]
    assert day["status"] == "warn" and "weather: TypeError" in day["message"]
    assert "1 on-this-day items" in day["message"]
    assert db.get_edition(db.connect(db_path), eid).status == "built"


@respx.mock
def test_preview_is_not_recorded_and_does_not_eat_tomorrows_news(setup):
    mock_world(respx.mock)
    db_path, data_dir = setup
    eid = pipeline.build_edition(db_path, data_dir, services(), print_it=False, preview=True)
    conn = db.connect(db_path)
    ed = db.get_edition(conn, eid)
    assert ed.is_preview and ed.status == "built"
    assert db.seen_keys(conn) == set()
    assert (data_dir / "editions" / "0001" / "placed.json").exists()
    real = pipeline.build_edition(db_path, data_dir, services(), print_it=False)
    assert not db.get_edition(conn, real).is_preview
    assert len(db.seen_keys(conn)) >= 2  # the same stories were still available to the real paper
    assert "Yesterday" not in (data_dir / "editions" / "0002" / "edition.html").read_text()


def _placed(data_dir, number=1):
    return json.loads((data_dir / "editions" / f"{number:04d}" / "placed.json").read_text())


@respx.mock
def test_every_edition_lists_its_pieces_in_print_order_with_original_urls(setup):
    mock_world(respx.mock)
    db_path, data_dir = setup
    for preview in (True, False):
        pipeline.build_edition(db_path, data_dir, services(), print_it=False, preview=preview)
    for number in (1, 2):
        data = _placed(data_dir, number)
        pieces = data["pieces"]
        assert pieces and pieces[0]["kind"] == "lead"
        for p in pieces:
            assert {"section", "kind", "headline", "source", "url", "key"} <= set(p)
            assert p["url"].startswith("https://news.example.com/") and p["key"] == p["url"]
        assert data["also"] == []


@respx.mock
def test_placed_json_lists_the_also_in_the_news_headlines(setup):
    mock_world(respx.mock)
    respx.mock.get("https://feeds.example.com/news").mock(return_value=httpx.Response(200, text=rss("n", 3, "Wire")))
    db_path, data_dir = setup
    conn = db.connect(db_path)
    db.add_feed(conn, "https://feeds.example.com/news", "World", headlines_only=True)
    pipeline.build_edition(db_path, data_dir, services(llm=False), print_it=False)
    also = _placed(data_dir)["also"]
    assert also and set(also[0]) == {"title", "source", "url"}
    assert also[0]["url"].startswith("https://news.example.com/n/")


@respx.mock
def test_previews_are_skipped_for_answers_and_puzzle_alternation(setup, monkeypatch):
    mock_world(respx.mock)
    db_path, data_dir = setup
    numbers, answers = [], []
    monkeypatch.setattr(pipeline, "puzzle_kind", lambda mode, number: numbers.append(number) or "sudoku")
    real_render = pipeline.renderer.render_edition

    def spy(inp):
        answers.append(inp.answers)
        return real_render(inp)

    monkeypatch.setattr(pipeline.renderer, "render_edition", spy)
    first = pipeline.build_edition(db_path, data_dir, services(), print_it=False)
    pipeline.build_edition(db_path, data_dir, services(), print_it=False, preview=True)
    pipeline.build_edition(db_path, data_dir, services(), print_it=False)
    conn = db.connect(db_path)
    assert numbers == [1, 2, 2]  # a preview shows what the next real paper would get
    solution = json.loads(db.get_edition(conn, first).puzzle_solution)
    assert answers[0] is None and answers[1] == solution and answers[2] == solution


@respx.mock
def test_printing_a_preview_from_the_archive_records_its_stories(setup):
    mock_world(respx.mock)
    db_path, data_dir = setup
    eid = pipeline.build_edition(db_path, data_dir, services(), print_it=False, preview=True)
    conn = db.connect(db_path)
    pipeline.reprint(db_path, eid, services(FakePrinter(fail=True)))
    assert db.get_edition(conn, eid).is_preview and db.seen_keys(conn) == set()  # a failed print keeps it a preview
    printer = FakePrinter()
    pipeline.reprint(db_path, eid, services(printer))
    ed = db.get_edition(conn, eid)
    assert printer.printed and ed.status == "printed" and not ed.is_preview
    placed = json.loads((data_dir / "editions" / "0001" / "placed.json").read_text())["pieces"]
    assert db.seen_keys(conn) == {p["key"] for p in placed} and placed
    pipeline.reprint(db_path, eid, services(FakePrinter()))  # a second reprint does not record twice
    assert conn.execute("SELECT COUNT(*) FROM stories").fetchone()[0] == len(placed)


def test_build_edition_can_wait_for_the_lock(setup):
    import threading
    import time

    db_path, data_dir = setup
    pipeline.BUILD_LOCK.acquire()
    threading.Timer(0.3, pipeline.BUILD_LOCK.release).start()
    start = time.monotonic()
    with respx.mock as router:
        mock_world(router)
        eid = pipeline.build_edition(db_path, data_dir, services(), print_it=False, lock_timeout=30)
    assert time.monotonic() - start >= 0.25
    assert db.get_edition(db.connect(db_path), eid).status == "built"


def test_build_edition_gives_up_when_the_wait_runs_out(setup):
    db_path, data_dir = setup
    with pipeline.BUILD_LOCK:
        with pytest.raises(RuntimeError):
            pipeline.build_edition(db_path, data_dir, services(), print_it=False, lock_timeout=0.05)


class MovingLLM(FakeLLM):
    """Sends every primary pick to World and records the selection prompt."""

    def __init__(self):
        self.prompts = []

    def ask_json(self, system, user, schema, max_tokens=16000):
        sel = super().ask_json(system, user, schema, max_tokens)
        if schema is editor.Selection:
            self.prompts.append(user)
            sel.primary = [editor.Pick(id=p.id, length=p.length, section="World") for p in sel.primary]
        return sel


@respx.mock
def test_a_pick_moved_to_another_section_prints_there_and_the_prompt_names_home(setup):
    mock_world(respx.mock)
    db_path, data_dir = setup
    llm = MovingLLM()
    svc = pipeline.Services(http=httpx.Client(), llm_factory=lambda s: llm, printer=FakePrinter(),
                            now=lambda: NOW, rng=random.Random(5))
    pipeline.build_edition(db_path, data_dir, svc, print_it=False)
    assert "Reader's home: Testville" in llm.prompts[0]
    pieces = json.loads((data_dir / "editions" / "0001" / "pieces.json").read_text())
    sel = json.loads((data_dir / "editions" / "0001" / "selection.json").read_text())
    moved = {p["id"] for p in sel["primary"]}
    primary_pieces = [p for p in pieces if p["item_id"] in moved]
    assert primary_pieces and all(p["section"] == "World" for p in primary_pieces)
