import json
import random
import threading
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from . import db, imposition
from .layout import renderer
from .llm import editor
from .llm.client import LLM, LLMError
from .models import Piece
from .printing import PrintError
from .puzzles import crossword, sudoku
from .settings import Settings
from .sources import articles, calendar, feeds, onthisday, weather

BUILD_LOCK = threading.Lock()


def _claude_warning(llm, since: int = 0) -> str | None:
    """A one-line summary of Claude failures since `since`; error text comes from the API,
    which never echoes the key."""
    errors = list(getattr(llm, "errors", []) or [])[since:]
    if not errors:
        return None
    return f"Claude failed {len(errors)} time{'s' if len(errors) != 1 else ''}; first error: {errors[0][:240]}"


@dataclass
class Services:
    http: httpx.Client
    llm_factory: Callable[[Settings], LLM | None]
    printer: object  # anything with ensure_queue(address) and print_pdf(path) -> job id
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    rng: random.Random = field(default_factory=random.Random)


@dataclass
class _Step:
    message: str = ""
    warnings: list[str] = field(default_factory=list)


class StepLog:
    def __init__(self, conn, edition_id: int):
        self.conn, self.edition_id = conn, edition_id

    @contextmanager
    def step(self, name: str) -> Iterator[_Step]:
        rec = _Step()
        try:
            yield rec
        except Exception as exc:  # noqa: BLE001 - one failed step must never cost the whole paper
            db.append_log(self.conn, self.edition_id, name, "failed", f"{type(exc).__name__}: {exc}")
        else:
            status = "warn" if rec.warnings else "ok"
            db.append_log(self.conn, self.edition_id, name, status,
                          "; ".join(x for x in [rec.message, *rec.warnings] if x) or "done")


def puzzle_kind(mode: str, number: int) -> str:
    if mode in ("sudoku", "crossword"):
        return mode
    return "sudoku" if number % 2 == 0 else "crossword"


def build_edition(db_path: Path, data_dir: Path, services: Services, print_it: bool,
                  preview: bool = False, lock_timeout: float | None = None) -> int:
    """Build one edition. A preview is never recorded in story history and never used for the
    answers or puzzle alternation, until it is printed from the archive. With `lock_timeout`
    set, wait up to that many seconds for a running build instead of refusing at once."""
    acquired = (BUILD_LOCK.acquire(blocking=False) if lock_timeout is None
                else BUILD_LOCK.acquire(timeout=lock_timeout))
    if not acquired:
        raise RuntimeError("An edition is already building")
    try:
        return _build(db_path, data_dir, services, print_it, preview)
    finally:
        BUILD_LOCK.release()


def _build(db_path: Path, data_dir: Path, services: Services, print_it: bool, preview: bool) -> int:
    conn = db.connect(db_path)
    try:
        s = db.load_settings(conn)
        tz = ZoneInfo(s.timezone)
        now = services.now()
        day = now.astimezone(tz).date()
        ed = db.create_edition(conn, day.isoformat(), preview=preview)
        try:
            _produce(conn, s, tz, now, day, ed, data_dir, services, print_it, preview)
        except Exception as exc:  # noqa: BLE001 - an edition must never be left in "building"
            # type and message only: the iCal URL is touched solely inside the guarded "day" step
            db.append_log(conn, ed.id, "build", "failed", f"{type(exc).__name__}: {exc}")
            db.update_edition(conn, ed.id, status="failed")
        return ed.id
    finally:
        conn.close()


def _produce(conn, s: Settings, tz: ZoneInfo, now: datetime, day, ed, data_dir: Path,
             services: Services, print_it: bool, preview: bool) -> None:
    work = data_dir / "editions" / f"{ed.number:04d}"
    work.mkdir(parents=True, exist_ok=True)
    log = StepLog(conn, ed.id)
    llm = services.llm_factory(s)

    items = []
    with log.step("gather") as st:
        feed_list = db.list_feeds(conn, enabled_only=True)
        result = feeds.gather(services.http, feed_list, now, db.seen_keys(conn))
        items = result.items
        st.message = f"{len(items)} new items from {len(feed_list)} feeds"
        st.warnings.extend(result.errors)
    by_id = {i.id: i for i in items}

    sel = editor.Selection(lead_id=None, good_news_id=None, primary=[], reserves=[], also=[])
    with log.step("select") as st:
        sel = editor.select(llm, items, s.sections, s.interests,
                            s.target_sheets * 2 - 2, s.max_sheets * 2 - 2,
                            s.home.label or s.home.query)
        st.message = f"lead {sel.lead_id}, {len(sel.primary)} primary, {len(sel.reserves)} reserves"
        if warning := _claude_warning(llm):
            st.warnings.append(warning)
    (work / "selection.json").write_text(sel.model_dump_json(indent=2))

    lead: Piece | None = None
    good: Piece | None = None
    primary: list[Piece] = []
    reserves: list[Piece] = []
    lead_image: Path | None = None
    with log.step("write") as st:
        errors_before = len(getattr(llm, "errors", []) or [])
        jobs = []
        if sel.lead_id is not None:
            jobs.append(("lead", by_id[sel.lead_id], "lead", None))
        if sel.good_news_id is not None:
            jobs.append(("good", by_id[sel.good_news_id], "good_news", None))
        jobs += [("primary", by_id[p.id], p.length, p.section) for p in sel.primary]
        jobs += [("reserve", by_id[p.id], p.length, p.section) for p in sel.reserves]

        def write_one(job):
            role, item, kind, section = job
            try:
                art = None if item.headlines_only else articles.fetch_article(services.http, item.url)
                piece = editor.condense(llm, item, art.text if art else None, kind)
                if section:  # the editor moved this story out of its feed's section
                    piece = piece.model_copy(update={"section": section})
                return role, piece, art
            except Exception as exc:  # noqa: BLE001 - one bad article must not drop every piece
                # title and exception type only: messages can carry URLs
                return role, None, f"{item.title!r} ({type(exc).__name__})"

        with ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(write_one, jobs))
        results = [o for o in outcomes if o[1] is not None]
        for _, _, skipped in (o for o in outcomes if o[1] is None):
            st.warnings.append(f"skipped {skipped}")
        for role, piece, art in results:
            if role == "lead":
                lead = piece
                if art and art.image_url:
                    lead_image = articles.download_image(services.http, art.image_url, work / "lead-source.img")
            elif role == "good":
                good = piece
            elif role == "primary":
                primary.append(piece)
            else:
                reserves.append(piece)
        if good is None:
            good = next((p for p in reserves if p.kind != "brief"), None)
            if good is not None:
                reserves.remove(good)
        fallbacks = sum(1 for _, p, _ in results if p.kind == "brief")
        st.message = f"{len(results)} pieces written, {fallbacks} as briefs"
        if warning := _claude_warning(llm, errors_before):
            st.warnings.append(warning)
    (work / "pieces.json").write_text(json.dumps(
        [p.model_dump() for p in ([lead] if lead else []) + ([good] if good else []) + primary + reserves],
        indent=2))

    wx = None
    events = []
    otd = []
    with log.step("day") as st:
        home = s.home
        if home.latitude is not None and home.longitude is not None:
            try:
                wx = weather.fetch_weather(services.http, home.latitude, home.longitude, s.timezone,
                                           expanded=s.expanded_weather, units=s.weather_units)
            except Exception as exc:  # noqa: BLE001 - one failed sub-source must not drop the others
                detail = str(exc) if isinstance(exc, httpx.HTTPError) else type(exc).__name__
                st.warnings.append(f"weather: {detail}")
        for cal in s.calendars:
            try:
                events += calendar.events_for_day(calendar.fetch_ical(services.http, cal.url), day, tz)
            except Exception:  # noqa: BLE001 - httpx.InvalidURL and parser errors are not HTTPError/ValueError
                # name only, never the exception text: it can contain the private calendar URL
                st.warnings.append(f'calendar "{cal.name}": could not load')
        events.sort(key=lambda e: (e.sort_minute, e.title))
        try:
            otd = onthisday.fetch_on_this_day(services.http, day)
        except Exception:  # noqa: BLE001
            st.warnings.append("on this day: could not load")
        st.message = f"weather {'ok' if wx else 'missing'}, {len(events)} events, {len(otd)} on-this-day items"

    puzzle = None
    with log.step("puzzle") as st:
        kind = puzzle_kind(s.puzzle_mode, db.next_daily_number(conn, ed.id))
        solution = None
        if kind == "crossword":
            try:
                fill = crossword.build_crossword(crossword.word_index(), services.rng) if llm else None
                if fill is None:
                    st.warnings.append("crossword unavailable, using sudoku")
                else:
                    clues = editor.write_clues(llm, [(x.number, x.direction, x.answer) for x in fill.slots])
                    puzzle = renderer.PuzzleView(kind="crossword", crossword=fill, clues=clues)
                    solution = {"kind": "crossword", "grid": fill.grid}
            except Exception as exc:  # noqa: BLE001 - the sudoku fallback must always run
                detail = str(exc) if isinstance(exc, LLMError) else type(exc).__name__
                st.warnings.append(f"crossword failed ({detail}), using sudoku")
        if puzzle is None:
            grid = sudoku.generate(services.rng, s.sudoku_difficulty)
            puzzle = renderer.PuzzleView(kind="sudoku", sudoku=grid)
            solution = {"kind": "sudoku", "solution": grid.solution}
        db.update_edition(conn, ed.id, puzzle_type=puzzle.kind, puzzle_solution=json.dumps(solution))
        st.message = puzzle.kind
    prev = db.previous_answers_edition(conn, ed.number)
    answers = json.loads(prev.puzzle_solution) if prev and prev.puzzle_solution else None

    pdf_path = work / "edition.pdf"
    pdf_path.unlink(missing_ok=True)  # a stale file from an earlier attempt must not count
    built = False
    with log.step("layout") as st:
        inp = renderer.EditionInput(
            settings=s, day=day, number=ed.number, work_dir=work, weather=wx, events=events,
            on_this_day=otd, lead=lead, lead_image=lead_image, good_news=good, primary=primary,
            reserves=reserves, also=[by_id[i] for i in sel.also if i in by_id],
            puzzle=puzzle, answers=answers)
        out = renderer.render_edition(inp)
        pdf_path.write_bytes(imposition.impose(out.pdf))
        (work / "edition.html").write_text(out.html)
        ranks = {p.key: i for i, p in enumerate(([lead] if lead else []) + primary + reserves)}
        (work / "placed.json").write_text(json.dumps({
            # good_news marks a promoted reserve, whose own kind and section say nothing about Good News
            "pieces": [{**p.model_dump(), **({"good_news": True} if good is not None and p is good else {})}
                       for p in out.placed],
            "ranks": ranks,
            "also": [{"title": i.title, "source": i.source, "url": i.url} for i in out.also],
        }, indent=2))
        if not preview:
            db.record_stories(conn, ed.id, out.placed, ranks)
        db.update_edition(conn, ed.id, status="built", pdf_path=str(pdf_path))
        st.message = f"{out.pages} pages, {len(out.placed)} pieces placed"
        if out.dropped:
            st.warnings.append(f"{len(out.dropped)} pieces did not fit")
        built = True
    if not built:
        db.update_edition(conn, ed.id, status="failed")
        return

    if print_it:
        _print(conn, ed.id, s, pdf_path, services)


def _print(conn, edition_id: int, s: Settings, pdf_path: Path, services: Services) -> None:
    try:
        services.printer.ensure_queue(s.printer_address)
        job = services.printer.print_pdf(pdf_path)
    except PrintError as exc:
        db.update_edition(conn, edition_id, status="print_failed")
        db.append_log(conn, edition_id, "print", "failed", str(exc))
        return
    except Exception as exc:  # noqa: BLE001 - the PDF is kept; reprint can retry
        db.update_edition(conn, edition_id, status="print_failed")
        db.append_log(conn, edition_id, "print", "failed", f"{type(exc).__name__}: {exc}")
        return
    db.update_edition(conn, edition_id, status="printed")
    db.append_log(conn, edition_id, "print", "ok", f"sent to printer as {job}")


def _record_preview_stories(conn, ed: db.Edition) -> None:
    """A preview that gets printed becomes a real edition: its stories now count as seen."""
    placed = Path(ed.pdf_path).parent / "placed.json"
    if placed.exists():
        data = json.loads(placed.read_text())
        db.record_stories(conn, ed.id, [Piece.model_validate(p) for p in data["pieces"]], data["ranks"])
    db.update_edition(conn, ed.id, is_preview=False)


def reprint(db_path: Path, edition_id: int, services: Services) -> None:
    conn = db.connect(db_path)
    try:
        ed = db.get_edition(conn, edition_id)
        if ed is None or not ed.pdf_path or not Path(ed.pdf_path).exists():
            raise ValueError("That edition has no PDF to print")
        _print(conn, edition_id, db.load_settings(conn), Path(ed.pdf_path), services)
        if ed.is_preview and db.get_edition(conn, edition_id).status == "printed":
            _record_preview_stories(conn, ed)
    finally:
        conn.close()
