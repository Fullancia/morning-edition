import logging
import os
import sqlite3
import sys
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from functools import lru_cache
from itertools import count
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import available_timezones

import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from starlette.middleware.sessions import SessionMiddleware

from .. import db, pipeline, scheduler, updates
from ..config import AppConfig, load_config
from ..layout.renderer import FONTS, MASTHEAD_WEIGHTS, render_test_page
from ..llm.client import LLM, LLMError
from ..printing import CupsPrinter, PrintError
from ..settings import GOOD_NEWS, MASTHEAD_FONTS, MODEL_CHOICES, CalendarLink, Settings, WeatherLocation
from ..sources import discover as discover_mod
from ..sources import feeds as feeds_mod
from ..sources import weather
from ..sources.http import public_client
from . import discovery
from .auth import check_password
from .paper import is_web_url, todays_paper

HERE = Path(__file__).parent
SCHEDULED_WAIT_SECONDS = 30 * 60


class LoginRequired(Exception):
    pass


def require_login(request: Request) -> None:
    if not request.session.get("auth"):
        raise LoginRequired()


def _configure_logging() -> None:
    """INFO to stdout under the "morning_edition" logger so `docker compose logs` shows pipeline trouble."""
    logger = logging.getLogger("morning_edition")
    logger.setLevel(logging.INFO)
    if not any(getattr(h, "_morning_edition", False) for h in logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        handler._morning_edition = True  # type: ignore[attr-defined]
        logger.addHandler(handler)


US_TIMEZONES = [
    ("America/New_York", "Eastern (New York)"), ("America/Chicago", "Central (Chicago)"),
    ("America/Denver", "Mountain (Denver)"), ("America/Phoenix", "Mountain, no daylight time (Phoenix)"),
    ("America/Los_Angeles", "Pacific (Los Angeles)"), ("America/Anchorage", "Alaska (Anchorage)"),
    ("Pacific/Honolulu", "Hawaii (Honolulu)"),
]


_LEGACY_PREFIXES = ("Etc/", "posix/", "right/", "US/", "Canada/", "Brazil/", "Chile/", "Mexico/")


@lru_cache(maxsize=1)
def _all_timezones() -> tuple[str, ...]:
    """Region names plus UTC, without Etc/posix/right, the legacy US/Canada/Brazil/Chile/Mexico aliases,
    Antarctica/South_Pole, or bare legacy names. The tz database does not change while the app runs."""
    us = {name for name, _ in US_TIMEZONES}
    return tuple(sorted(z for z in available_timezones()
                        if z not in us and not z.startswith(_LEGACY_PREFIXES) and z != "Antarctica/South_Pole"
                        and ("/" in z or z == "UTC")))


def timezone_choices(saved: str) -> list[str]:
    """Every zone for the "All timezones" group."""
    zones = set(_all_timezones())
    if saved not in {name for name, _ in US_TIMEZONES}:
        zones.add(saved)  # a zone saved before this list existed still shows as selected
    return sorted(zones)


def _is_calendar_url(url: str) -> bool:
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False
    return parts.scheme.lower() in ("http", "https", "webcal") and bool(parts.netloc)


CALENDAR_NAME_MAX = 60


def _default_calendar_name(calendars: list[CalendarLink]) -> str:
    """The smallest "Calendar N" not already in use."""
    used = {c.name for c in calendars}
    return next(f"Calendar {n}" for n in count(1) if f"Calendar {n}" not in used)


def default_services() -> pipeline.Services:
    def llm_factory(s: Settings):
        return LLM(s.model, s.effort) if os.environ.get("ANTHROPIC_API_KEY") else None

    # retries=3 covers connection-level failures; sources.http.get_with_retry covers timeouts and 5xx
    http = httpx.Client(transport=httpx.HTTPTransport(retries=3))
    return pipeline.Services(http=http, llm_factory=llm_factory, printer=CupsPrinter())


def create_app(config: AppConfig | None = None, services: pipeline.Services | None = None,
               start_scheduler: bool = True) -> FastAPI:
    _configure_logging()
    config = config or load_config()
    config.data_dir.mkdir(parents=True, exist_ok=True)
    db_path = config.data_dir / "morning-edition.sqlite"
    db.connect(db_path).close()
    services = services or default_services()
    templates = Jinja2Templates(directory=HERE / "templates")

    def run_build(print_it: bool, preview: bool = False) -> None:
        try:
            pipeline.build_edition(db_path, config.data_dir, services, print_it, preview=preview)
        except RuntimeError:
            pass  # a build is already running; the dashboard shows it

    def scheduled_job() -> None:
        """The daily paper waits for a running build (a preview, say) rather than skipping the day."""
        conn = db.connect(db_path)
        try:
            auto = db.load_settings(conn).auto_print
        finally:
            conn.close()
        try:
            pipeline.build_edition(db_path, config.data_dir, services, auto, preview=False,
                                   lock_timeout=SCHEDULED_WAIT_SECONDS)
        except RuntimeError:
            logging.getLogger("morning_edition").error(
                "Scheduled edition skipped: another build was still running after %d minutes",
                SCHEDULED_WAIT_SECONDS // 60)

    def nightly_update_job() -> None:
        conn = db.connect(db_path)
        try:
            s = db.load_settings(conn)
        finally:
            conn.close()
        updates.nightly_update(services.http, config, s, pipeline.BUILD_LOCK.locked())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cutoff = time.time() - 86400
        for stale in config.data_dir.glob("tmp*.pdf"):
            if stale.stat().st_mtime < cutoff:
                stale.unlink(missing_ok=True)
        if start_scheduler:
            sched = BackgroundScheduler()
            sched.start()
            conn = db.connect(db_path)
            saved = db.load_settings(conn)
            conn.close()
            scheduler.schedule(sched, saved, scheduled_job)
            scheduler.schedule_update(sched, saved, nightly_update_job)
            app.state.scheduler = sched
        yield
        if app.state.scheduler is not None:
            app.state.scheduler.shutdown(wait=False)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SessionMiddleware, secret_key=config.session_secret, same_site="lax",
                       max_age=60 * 60 * 24 * 30)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    app.mount("/fonts", StaticFiles(directory=FONTS), name="fonts")  # public OFL files, for the font previews
    app.state.config = config
    app.state.db_path = db_path
    app.state.services = services
    app.state.scheduler = None
    app.state.last_thread = None
    app.state.discover_lock = threading.Lock()  # held while a feed search runs; separate from BUILD_LOCK

    def start_thread(target, *args) -> None:
        thread = threading.Thread(target=target, args=args, daemon=True)
        thread.start()
        app.state.last_thread = thread

    app.state.start_thread = start_thread
    app.state.run_build = run_build
    app.state.scheduled_job = scheduled_job
    app.state.nightly_update_job = nightly_update_job

    @app.exception_handler(LoginRequired)
    async def _to_login(request: Request, exc: LoginRequired):
        return RedirectResponse("/login", status_code=303)

    def page(request: Request, name: str, **ctx) -> Response:
        return templates.TemplateResponse(request, name, ctx)

    def updates_context(s: Settings) -> dict:
        """What the Updates section shows. Never includes the updater token."""
        status = updates.load_status(config.data_dir)
        version = config.app_version
        return {"configured": updates.configured(config), "status": status,
                "version": version if version and version != "dev" else "",
                "commit": config.app_commit[:7],
                "commits_url": updates.commits_url(config.app_repo),
                "checked": updates.format_when(status.checked_at, s.timezone),
                "requested": updates.format_when(status.update_requested_at, s.timezone),
                "auto": updates.format_when(status.auto_at, s.timezone)}

    def settings_page(request: Request, conn, message: str = "", error: str = "") -> Response:
        s = db.load_settings(conn)
        return page(request, "settings.html", s=s, feeds=db.list_feeds(conn), fonts=MASTHEAD_FONTS,
                    font_weights=MASTHEAD_WEIGHTS,
                    models=MODEL_CHOICES + ([(s.model, s.model)] if s.model not in dict(MODEL_CHOICES) else []),
                    us_timezones=US_TIMEZONES, timezones=timezone_choices(s.timezone),
                    sections=s.sections + [GOOD_NEWS], message=message, error=error, upd=updates_context(s),
                    next_run=scheduler.next_run(app.state.scheduler) if app.state.scheduler else None)

    @app.get("/login")
    def login_form(request: Request):
        return page(request, "login.html", error="")

    @app.post("/login")
    def login(request: Request, password: str = Form("")):
        if not check_password(password, config.dashboard_password):
            return page(request, "login.html", error="Wrong password.")
        request.session["auth"] = True
        return RedirectResponse("/", status_code=303)

    @app.post("/logout")
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/settings", dependencies=[Depends(require_login)])
    def settings_get(request: Request, added: str = ""):
        conn = db.connect(db_path)
        try:
            message = ""
            n = discovery.parse_index(added)
            if n is not None:
                message = f"Added {n} feed{'' if n == 1 else 's'}." if n else "No new feeds were added."
            return settings_page(request, conn, message=message)
        finally:
            conn.close()

    @app.post("/settings", dependencies=[Depends(require_login)])
    async def settings_post(request: Request):
        form = await request.form()
        conn = db.connect(db_path)
        try:
            current = db.load_settings(conn)
            data = {k: form.get(k, "") for k in (
                "paper_name", "masthead_font", "timezone", "weather_units", "print_time", "target_sheets",
                "max_sheets", "interests", "puzzle_mode", "sudoku_difficulty", "printer_address",
                "model", "effort", "halftone_gamma")}
            data["sections"] = [x.strip() for x in str(form.get("sections", "")).split(",")]
            data["auto_print"] = form.get("auto_print") == "on"
            data["expanded_weather"] = form.get("expanded_weather") == "on"
            # the overnight-update controls only exist when an updater is set up; otherwise keep what's saved
            data["auto_update"] = (form.get("auto_update") == "on") if "update_time" in form else current.auto_update
            data["update_time"] = str(form.get("update_time") or current.update_time)
            query = str(form.get("home_location", "")).strip()
            others = current.weather_locations[1:]

            def with_home(home: WeatherLocation) -> list[WeatherLocation]:
                return [home, *others] if home != WeatherLocation() or others else []

            data.update(calendars=current.calendars, build_lead_minutes=current.build_lead_minutes,
                        weather_locations=with_home(current.home.model_copy(update={"query": query})))
            try:
                updated = Settings(**data)
            except ValidationError as exc:
                problems = "\n".join(f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors())
                return settings_page(request, conn, error=problems)
            if query and query != current.home.query:
                try:
                    found = weather.geocode(services.http, query)
                except httpx.HTTPError:
                    found = None
                if found is None:
                    return settings_page(request, conn, error="Couldn't find that location.")
                located = WeatherLocation(query=query, label=found.label, latitude=found.latitude,
                                          longitude=found.longitude)
                updated = updated.model_copy(update={"weather_locations": with_home(located)})
            db.save_settings(conn, updated)
            if app.state.scheduler is not None:
                scheduler.schedule(app.state.scheduler, updated, scheduled_job)
                scheduler.schedule_update(app.state.scheduler, updated, nightly_update_job)
            return settings_page(request, conn, message="Saved.")
        finally:
            conn.close()

    @app.post("/calendars", dependencies=[Depends(require_login)])
    def calendar_add(name: str = Form(""), url: str = Form("")):
        name = name.strip()[:CALENDAR_NAME_MAX].strip()
        if _is_calendar_url(url) and not name.lower().startswith(("http", "webcal")):
            conn = db.connect(db_path)
            try:
                s = db.load_settings(conn)
                link = CalendarLink(name=name or _default_calendar_name(s.calendars), url=url.strip())
                db.save_settings(conn, s.model_copy(update={"calendars": [*s.calendars, link]}))
            finally:
                conn.close()
        return RedirectResponse("/settings#calendars", status_code=303)

    @app.post("/calendars/{index}/delete", dependencies=[Depends(require_login)])
    def calendar_delete(index: int):
        conn = db.connect(db_path)
        try:
            s = db.load_settings(conn)
            if 0 <= index < len(s.calendars):
                kept = [c for i, c in enumerate(s.calendars) if i != index]
                db.save_settings(conn, s.model_copy(update={"calendars": kept}))
        finally:
            conn.close()
        return RedirectResponse("/settings#calendars", status_code=303)

    @app.post("/feeds", dependencies=[Depends(require_login)])
    def feed_add(url: str = Form(...), section: str = Form(...)):
        if not is_web_url(url):
            return RedirectResponse("/settings#feeds", status_code=303)  # only web feeds can be fetched
        conn = db.connect(db_path)
        try:
            db.add_feed(conn, url, section, headlines_only=feeds_mod.is_google_news(url))
        except sqlite3.IntegrityError:
            pass  # already in the list
        finally:
            conn.close()
        return RedirectResponse("/settings#feeds", status_code=303)

    @app.post("/feeds/{feed_id}/toggle", dependencies=[Depends(require_login)])
    def feed_toggle(feed_id: int):
        conn = db.connect(db_path)
        try:
            feed = next((f for f in db.list_feeds(conn) if f.id == feed_id), None)
            if feed:
                db.set_feed_enabled(conn, feed_id, not feed.enabled)
        finally:
            conn.close()
        return RedirectResponse("/settings#feeds", status_code=303)

    @app.post("/feeds/{feed_id}/delete", dependencies=[Depends(require_login)])
    def feed_delete(feed_id: int):
        conn = db.connect(db_path)
        try:
            db.delete_feed(conn, feed_id)
        finally:
            conn.close()
        return RedirectResponse("/settings#feeds", status_code=303)

    @app.post("/feeds/google-news", dependencies=[Depends(require_login)])
    def feed_google_news():
        conn = db.connect(db_path)
        try:
            s = db.load_settings(conn)
            town = s.home.label or s.home.query
            if town:
                section = s.sections[0]
                try:
                    db.add_feed(conn, feeds_mod.google_news_url(town), section, headlines_only=True)
                except sqlite3.IntegrityError:
                    pass  # already added
        finally:
            conn.close()
        return RedirectResponse("/settings#feeds", status_code=303)

    @app.get("/feeds/discover", dependencies=[Depends(require_login)])
    def discover_get(request: Request, busy: str = ""):
        conn = db.connect(db_path)
        try:
            s = db.load_settings(conn)
        finally:
            conn.close()
        state = discovery.load_state(config.data_dir, app.state.discover_lock.locked())
        valid = s.sections + [GOOD_NEWS]
        done = state is not None and state["status"] == "done"
        return page(request, "discover.html", has_key=services.llm_factory(s) is not None, state=state, busy=bool(busy),
                    groups=discovery.group_by_section(state["results"], valid) if done else [],
                    sections=valid, summary_line=discovery.summary_line(state["summary"]) if done else "")

    def run_discovery(llm, s: Settings, existing: list[db.Feed], started: str, run_id: str) -> None:
        """Background thread: search, verify, and save the outcome for the page to show."""
        data_dir = config.data_dir
        try:
            # its own client: the sites come from web research, so they must never reach the local network
            with public_client() as http:
                found = discover_mod.discover(llm, http, s.interests, s.home.label or s.home.query,
                                              s.sections, existing, services.now())
            discovery.save_state(data_dir, "done", started, run_id, services.now().isoformat(),
                                 results=found.feeds, summary=found.summary)
        except LLMError as exc:
            discovery.save_state(data_dir, "error", started, run_id, services.now().isoformat(),
                                 error=str(exc)[:300])
        except Exception:  # noqa: BLE001 - the page shows a generic message; details go to the log
            logging.getLogger("morning_edition").exception("Feed search failed")
            discovery.save_state(data_dir, "error", started, run_id, services.now().isoformat(),
                                 error="The search failed. Check the logs for details.")
        finally:
            app.state.discover_lock.release()

    @app.post("/feeds/discover", dependencies=[Depends(require_login)])
    def discover_post():
        conn = db.connect(db_path)
        try:
            s = db.load_settings(conn)
            existing = db.list_feeds(conn)
        finally:
            conn.close()
        llm = services.llm_factory(s)
        if llm is not None and app.state.discover_lock.acquire(blocking=False):
            try:
                started = services.now().isoformat()
                run_id = uuid.uuid4().hex
                discovery.save_state(config.data_dir, "running", started, run_id)
                app.state.start_thread(run_discovery, llm, s, existing, started, run_id)
            except BaseException:
                app.state.discover_lock.release()
                raise
        return RedirectResponse("/feeds/discover", status_code=303)

    @app.post("/feeds/discover/add", dependencies=[Depends(require_login)])
    async def discover_add(request: Request):
        form = await request.form()
        if app.state.discover_lock.locked():  # the results on screen may already be replaced
            return RedirectResponse("/feeds/discover?busy=1", status_code=303)
        conn = db.connect(db_path)
        added = 0
        try:
            s = db.load_settings(conn)
            state = discovery.load_state(config.data_dir, app.state.discover_lock.locked())
            # the form only names rows by position, so it must belong to the result set it was rendered from
            if state and state["status"] == "done" and state["run_id"] and form.get("run") == state["run_id"]:
                valid = s.sections + [GOOD_NEWS]
                have = {feeds_mod.normalize_url(f.url) for f in db.list_feeds(conn)}
                for raw in form.getlist("pick"):
                    index = discovery.parse_index(raw)
                    if index is None or index >= len(state["results"]):
                        continue
                    result = state["results"][index]
                    key = feeds_mod.normalize_url(result.feed_url)
                    if not is_web_url(result.feed_url) or key in have:
                        continue
                    chosen = form.get(f"section_{index}")
                    section = chosen if chosen in valid else (result.section if result.section in valid else valid[0])
                    try:
                        db.add_feed(conn, result.feed_url, section, headlines_only=result.headlines_only)
                    except sqlite3.IntegrityError:
                        continue
                    have.add(key)
                    added += 1
        finally:
            conn.close()
        return RedirectResponse(f"/settings?added={added}#feeds", status_code=303)

    @app.get("/", dependencies=[Depends(require_login)])
    def today(request: Request):
        conn = db.connect(db_path)
        try:
            latest = next(iter(db.list_editions(conn, limit=1)), None)
            s = db.load_settings(conn)
            return page(request, "today.html", ed=latest, building=pipeline.BUILD_LOCK.locked(), s=s,
                        has_feeds=bool(db.list_feeds(conn)),
                        paper=todays_paper(conn, latest, config.data_dir, s.sections),
                        next_run=scheduler.next_run(app.state.scheduler) if app.state.scheduler else None)
        finally:
            conn.close()

    @app.post("/build", dependencies=[Depends(require_login)])
    def build_now():
        if not pipeline.BUILD_LOCK.locked():
            app.state.start_thread(run_build, False, True)
        return RedirectResponse("/", status_code=303)

    @app.get("/editions", dependencies=[Depends(require_login)])
    def archive(request: Request):
        conn = db.connect(db_path)
        try:
            return page(request, "archive.html", editions=db.list_editions(conn, limit=365))
        finally:
            conn.close()

    @app.get("/editions/{edition_id}/pdf", dependencies=[Depends(require_login)])
    def edition_pdf(edition_id: int):
        conn = db.connect(db_path)
        try:
            ed = db.get_edition(conn, edition_id)
        finally:
            conn.close()
        if ed is None or not ed.pdf_path:
            return Response(status_code=404)
        path = Path(ed.pdf_path).resolve()
        if not path.is_relative_to(config.data_dir.resolve()) or not path.exists():
            return Response(status_code=404)
        return FileResponse(path, media_type="application/pdf",
                            headers={"Content-Disposition": f'inline; filename="edition-{ed.number}.pdf"'})

    @app.post("/editions/{edition_id}/print", dependencies=[Depends(require_login)])
    def edition_print(edition_id: int):
        def job():
            try:
                pipeline.reprint(db_path, edition_id, services)
            except ValueError:
                pass  # no PDF; the archive page shows the status
        app.state.start_thread(job)
        return RedirectResponse("/editions", status_code=303)

    @app.post("/updates/check", dependencies=[Depends(require_login)])
    def updates_check(request: Request):
        if not updates.configured(config):
            return _settings_with(request, error="The updater service is not set up. See the README.")
        updates.run_check(services.http, config)
        return RedirectResponse("/settings#updates", status_code=303)

    @app.post("/updates/apply", dependencies=[Depends(require_login)])
    def updates_apply(request: Request):
        if not updates.configured(config):
            return _settings_with(request, error="The updater service is not set up. See the README.")
        if pipeline.BUILD_LOCK.locked():
            return _settings_with(request, error="An edition is building right now. Try the update again "
                                                 "when it finishes.")
        app.state.start_thread(updates.request_update, services.http, config.watchtower_url,
                               config.watchtower_token, config.app_image, config.data_dir)
        return page(request, "updating.html")

    def _settings_with(request: Request, error: str) -> Response:
        conn = db.connect(db_path)
        try:
            return settings_page(request, conn, error=error)
        finally:
            conn.close()

    @app.post("/printer/test", dependencies=[Depends(require_login)])
    def printer_test(request: Request):
        conn = db.connect(db_path)
        try:
            s = db.load_settings(conn)
            message, error = "Test page sent to the printer.", ""
            tmp_path = None
            try:
                pdf = render_test_page(s, datetime.now().strftime("%b %d, %Y %I:%M %p"))
                with tempfile.NamedTemporaryFile(suffix=".pdf", dir=config.data_dir, delete=False) as fh:
                    fh.write(pdf)
                    tmp_path = Path(fh.name)
                services.printer.ensure_queue(s.printer_address)
                services.printer.print_pdf(tmp_path)
            except Exception as exc:  # render or print failures show on the page instead of a 500
                message, error = "", f"Printer problem: {exc}"
            finally:
                if tmp_path is not None:
                    tmp_path.unlink(missing_ok=True)
            return settings_page(request, conn, message=message, error=error)
        finally:
            conn.close()

    return app
