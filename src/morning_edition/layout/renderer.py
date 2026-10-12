import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import weasyprint
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from ..models import Event, FeedItem, OnThisDay, Piece, Weather
from ..puzzles.crossword import CrosswordFill
from ..puzzles.sudoku import Sudoku
from ..settings import Settings
from . import geometry as g
from . import icons
from .halftone import halftone
from .columns import Balanced, balance
from .measure import Measured, heights, measure_blocks, widths
from .pack import Block, pack, plan_inside

HERE = Path(__file__).parent
TEMPLATES = HERE / "templates"
FONTS = HERE / "fonts"
MASTHEAD_WEIGHTS = {"Pirata One": 400, "Manufacturing Consent": 400, "Grenze Gotisch": 700,
                    "Playfair Display": 900}
LEAD_SIZES = (28, 24, 20)
NAMEPLATE_SIZES = (60, 54, 48, 42, 36, 30, 24, 20, 16, 12)
INDEX_STRIP_SIZES = (7.5, 7, 6.5)
EAR_TEMP_SIZES = (15, 14, 13, 12, 11, 10, 9)
EAR_ICON_W = 0.3 + 3 / 72      # the weather icon and the gap after it, inside the ear (.ear-temp)
MAX_EVENTS = 7
MAX_OUTLOOK = 3

_env = Environment(loader=FileSystemLoader(TEMPLATES),
                   autoescape=select_autoescape(enabled_extensions=("html", "j2")))
# the SVG is built only from icons.py constants and numbers, never feed text, so it is safe to mark as markup
_env.globals.update(
    icon=lambda kind, size="0.3in": Markup(icons.icon(kind, size)),
    moon=lambda illumination, waxing, size="0.5in": Markup(icons.moon(illumination, waxing, size)),
    kind_for_code=icons.kind_for_code)


@dataclass
class PuzzleView:
    kind: str
    sudoku: Sudoku | None = None
    crossword: CrosswordFill | None = None
    clues: dict[tuple[int, str], str] | None = None


@dataclass
class EditionInput:
    settings: Settings
    day: date
    number: int
    work_dir: Path
    weather: Weather | None
    events: list[Event]
    on_this_day: list[OnThisDay]
    lead: Piece | None
    lead_image: Path | None
    good_news: Piece | None
    primary: list[Piece]
    reserves: list[Piece]
    also: list[FeedItem]
    puzzle: PuzzleView | None
    answers: dict | None
    tomorrow_events: list[Event] = field(default_factory=list)


@dataclass
class RenderResult:
    html: str
    pdf: bytes
    pages: int
    placed: list[Piece]
    dropped: list[str]
    also: list[FeedItem] = field(default_factory=list)   # the "Also in the news" headlines that fit


def _common(settings: Settings | None = None) -> dict:
    ctx = {"g": g, "fonts_css": (FONTS / "fonts.css").as_uri(),
           "edition_css": (TEMPLATES / "edition.css").as_uri()}
    if settings is not None:
        ctx.update(s=settings, masthead_weight=MASTHEAD_WEIGHTS.get(settings.masthead_font, 400))
    return ctx


def _deg(weather: Weather | None) -> str:
    """The degree mark: bare in US mode (as it always was), with the scale letter in metric."""
    return "\u00b0C" if weather is not None and weather.units == "metric" else Markup("&deg;")


def _measure(items: list[tuple[str, float, str, object]]) -> dict[str, float]:
    """items are (key, width_in, macro_name, payload); returns heights in inches."""
    if not items:
        return {}
    return heights(_env.get_template("measure.html.j2").render(items=items, **_common()))


def _measure_blocks(items: list[tuple[str, float, str, object]]) -> dict[str, Measured]:
    if not items:
        return {}
    return measure_blocks(_env.get_template("measure.html.j2").render(items=items, **_common()))


def _measure_flow(paragraphs: list[str], macro: str, width: float = g.LEAD_COL_W) -> Measured:
    """A story's paragraphs laid out once at a text-column width (the front page's by default)."""
    html = _env.get_template("measure.html.j2").render(
        items=[("flow", width, macro, paragraphs)], **_common())
    return measure_blocks(html)["flow"]


def _fit_size(text: str, font: str, weight: int, sizes: tuple[float, ...], avail: float,
              index: list[tuple[str, int]] | None = None) -> float:
    """The largest size (pt) at which text sits on one line in avail inches; the smallest if none does.
    With index, the probe is the front-page index strip's own markup instead of plain text."""
    probes = [{"key": f"s{sz}", "text": text, "font": font, "weight": weight, "size": sz,
               **({"index": index} if index is not None else {})} for sz in sizes]
    html = _env.get_template("measure.html.j2").render(
        items=[(p["key"], 8.0, "text_probe", p) for p in probes], **_common())
    wide = widths(html)
    return next((sz for sz in sizes if wide[f"s{sz}"] <= avail), sizes[-1])


def _nameplate_size(settings: Settings, avail: float) -> int:
    return _fit_size(settings.paper_name, settings.masthead_font,
                     MASTHEAD_WEIGHTS.get(settings.masthead_font, 400), NAMEPLATE_SIZES, avail)


def _pd(p: Piece, **extra) -> dict:
    d = p.model_dump()
    d.update(extra)
    return d


def _lead_head(lead: Piece) -> tuple[dict, float]:
    head, head_h = None, 0.0
    for size in LEAD_SIZES:
        head = _pd(lead, size=size)
        head_h = _measure([("h", g.LEAD_W, "lead_header", head)])["h"]
        if head_h <= g.LEAD_HEADER_MAX:
            break
    else:
        head = _pd(lead, size=LEAD_SIZES[-1], deck="")
        head_h = _measure([("h", g.LEAD_W, "lead_header", head)])["h"]
    return head, head_h


def _lead_photo(lead: Piece, image: Path | None, work_dir: Path, gamma: float, y_text: float) -> dict | None:
    """The halftoned photo across the lead's second and third columns, if there is an image and room
    for it above the fold. Its block (photo and caption) is a whole number of text lines tall, so the
    text columns beside and below it keep to one line grid."""
    if image is None:
        return None
    lines = int((g.FOLD - 0.12 - y_text) / g.LINE + 1e-6)
    photo_h = min(g.PHOTO_MAX_H, lines * g.LINE - g.CAPTION_H)
    if photo_h < g.PHOTO_MIN_H:
        return None
    block_h = math.ceil((photo_h + g.CAPTION_H) / g.LINE - 1e-6) * g.LINE
    out = work_dir / "lead-halftone.png"
    try:
        halftone(image, out, g.PHOTO_W, photo_h, gamma=gamma)
    except OSError:
        return None
    return {"src": out.as_uri(), "height": round(photo_h, 3), "caption": f"Photo: {lead.source}",
            "x": round(g.lead_col_x(1), 4), "y": round(y_text, 4), "w": round(g.PHOTO_W, 4),
            "block_h": round(block_h, 4)}


def _windows(measured: Measured, bal: Balanced, y_top: float, offsets: list[float],
             col_w: float = g.LEAD_COL_W) -> tuple[list[dict], list[dict]]:
    """The text windows and the rules between them for a story poured into side-by-side columns."""
    def col_x(i: int) -> float:
        return g.MARGIN_X + i * (col_w + g.GUTTER)

    cols, rules = [], []
    for i, (lo, hi) in enumerate(bal.cols):
        if hi == lo:
            continue
        y0, h = bal.window(measured.lines, i)
        cols.append({"x": round(col_x(i), 4), "y": round(y_top + offsets[i], 4), "w": round(col_w, 4),
                     "h": round(h, 4), "y0": round(y0, 4)})
    for i in range(len(offsets) - 1):
        if bal.cols[i + 1][0] == bal.cols[i + 1][1]:
            continue
        rules.append({"x": round(col_x(i + 1) - g.GUTTER / 2, 4),
                      "y": round(y_top + min(offsets[i], offsets[i + 1]), 4),
                      "h": round(max(bal.bottoms[i], bal.bottoms[i + 1]) - min(offsets[i], offsets[i + 1]), 4)})
    return cols, rules


def _fit_front(lead: Piece | None, image: Path | None, work_dir: Path, gamma: float,
               story2_piece: Piece | None, bottom_h: float, wide: bool = False) -> dict:
    """Lay out the front page's body: the lead in three columns (the photo across the second and
    third), flowing on past the fold, and the second story below it in three columns. Both are poured
    as line-level windows onto one layout, trimmed at the foot, and balanced to end level.

    With wide (nothing fills the right-hand column below the fold) the second story spans the whole
    page width in four columns, starting below the Your Day rail."""
    y_bot = g.FRONT_BOTTOM_Y + bottom_h
    lead_view = None
    y_lead_end = g.FRONT_BOTTOM_Y - g.REGION_GAP       # where a second story would start with no lead above it
    lead_state = None
    if lead is not None:
        head, head_h = _lead_head(lead)
        y_text = g.FRONT_TOP_Y + head_h + g.LEAD_HEAD_GAP
        photo = _lead_photo(lead, image, work_dir, gamma, y_text)
        offsets = [0.0, photo["block_h"], photo["block_h"]] if photo else [0.0, 0.0, 0.0]
        measured = _measure_flow(lead.paragraphs, "lead_flow")
        lead_state = (head, head_h, y_text, photo, offsets, measured)

    def lead_balanced(cap_y: float) -> Balanced:
        head, head_h, y_text, photo, offsets, measured = lead_state
        return balance(measured.lines, offsets, cap_y - y_text, measured.para_ends)

    def lay_story2(piece: Piece) -> dict | None:
        """The second story under the lead, or None if there is no sensible room for it."""
        head = _pd(piece)
        ncols, col_w, head_w = ((g.WIDE_COLS, g.WIDE_COL_W, g.CONTENT_W) if wide
                                else (3, g.LEAD_COL_W, g.LEAD_W))
        zeros = [0.0] * ncols
        head_h = _measure([("h", head_w, "story_header", head)])["h"]
        measured = _measure_flow(piece.paragraphs, "story_flow", col_w)
        natural = balance(measured.lines, zeros, g.STORY2_TARGET_MAX_H, measured.para_ends)
        target = max(g.STORY2_MIN_H, min(head_h + 0.04 + natural.height, g.STORY2_TARGET_MAX_H))
        y_floor = y_bot - g.REGION_GAP - target            # the lead must end above this
        if lead_state:
            head_l, head_lh, y_text, photo, offsets, measured_l = lead_state
            need = max(offsets[1] + 3 * g.LINE, 6 * g.LINE)    # the photo block and a few lines, or six lines
            if y_floor - y_text < need:
                return None
            bal = lead_balanced(y_floor)
            y2 = y_text + bal.height + g.REGION_GAP
            if wide:
                y2 = max(y2, g.FRONT_BOTTOM_Y)             # clear of the rail above the fold's right-hand side
            elif y2 < g.FOLD + 0.04 and y2 + head_h > g.FOLD - 0.04:   # the head would cross the fold
                cap_c = g.FOLD - 0.04 - head_h - g.REGION_GAP
                if cap_c - y_text >= need:
                    trimmed = lead_balanced(cap_c)
                    cost_trim = bal.height - trimmed.height
                    cost_gap = (g.FOLD + 0.06) - y2
                    if cost_trim <= cost_gap:
                        bal, y2 = trimmed, y_text + trimmed.height + g.REGION_GAP
                    else:
                        y2 = g.FOLD + 0.06
                else:
                    y2 = g.FOLD + 0.06
            lead_bal = bal
        else:
            y2, lead_bal = g.FRONT_BOTTOM_Y, None
        body_cap = y_bot - y2 - head_h - 0.02
        if body_cap < 4 * g.LINE:
            return None
        bal2 = balance(measured.lines, zeros, body_cap, measured.para_ends)
        if bal2.shown == 0:
            return None
        cols, rules = _windows(measured, bal2, y2 + head_h + 0.02, zeros, col_w)
        return {"lead_bal": lead_bal, "view": {"head": head, "head_y": round(y2, 4), "head_h": round(head_h, 4),
                                               "head_w": round(head_w, 4), "paras": piece.paragraphs,
                                               "cols": cols, "rules": rules}}

    story2 = lay_story2(story2_piece) if story2_piece is not None else None
    used_story2 = story2_piece if story2 else None
    if lead_state:
        head, head_h, y_text, photo, offsets, measured = lead_state
        bal = story2["lead_bal"] if story2 else lead_balanced(y_bot)
        cols, rules = _windows(measured, bal, y_text, offsets)
        lead_view = {"head": head, "head_h": round(head_h, 4), "y_text": round(y_text, 4), "photo": photo,
                     "paras": lead.paragraphs, "cols": cols, "rules": rules}
    return {"lead": lead_view, "story2": story2["view"] if story2 else None, "story2_piece": used_story2}


def _fit_good(p: Piece) -> dict:
    """The Good News story across the back page's top left in three balanced windowed columns."""
    head = _pd(p)
    head_h = _measure([("h", g.GOOD_W, "good_header", head)])["h"]
    y_top = g.INSIDE_Y + head_h + g.LEAD_HEAD_GAP
    measured = _measure_flow(p.paragraphs, "story_flow")
    bal = balance(measured.lines, [0.0, 0.0, 0.0], g.INSIDE_Y + g.BACK_TOP_H - y_top, measured.para_ends)
    cols, rules = _windows(measured, bal, y_top, [0.0, 0.0, 0.0])
    return {"head": head, "head_h": round(head_h, 4), "paras": p.paragraphs, "cols": cols, "rules": rules}


def _fit_front_briefs(briefs: list[Piece], bottom_h: float = g.FRONT_BOTTOM_H) -> list[Piece]:
    if not briefs:
        return []
    hs = _measure([(f"b{i}", g.BRIEFS_TEXT_W, "brief", _pd(p)) for i, p in enumerate(briefs)])
    cols, _ = pack([Block(str(i), hs[f"b{i}"]) for i in range(len(briefs))],
                   bottom_h - g.KICKER_H, 1, g.GAP)
    chosen = {int(k) for c in cols for k in c.keys}
    return [p for i, p in enumerate(briefs) if i in chosen]


def _fit_also_side(items: list[FeedItem], bottom_h: float) -> int:
    """How many leading "Also in the news" headlines fit whole in the right-hand column below the fold."""
    if not items:
        return 0
    m = _measure_blocks([("a", g.BRIEFS_TEXT_W, "also_side", [i.model_dump() for i in items])])["a"]
    return sum(1 for _, bottom in m.lines if bottom <= bottom_h + 1e-6) if len(m.lines) == len(items) else 0


def _fit_side(events: list[Event], weather: Weather | None, tomorrow: list[Event] | None = None) -> dict:
    """Trim the front-page sidebar until it fits: tomorrow's events first, then outlook days, then today's."""
    tomorrow = tomorrow or []
    outlook = list(weather.outlook[:MAX_OUTLOOK]) if weather else []
    n_events = min(len(events), MAX_EVENTS)
    n_tomorrow = min(len(tomorrow), MAX_EVENTS)
    while True:
        side = {"events": events[:n_events], "more": len(events) - n_events,
                "tomorrow": tomorrow[:n_tomorrow], "tomorrow_more": len(tomorrow) - n_tomorrow if n_tomorrow else 0,
                "outlook": outlook, "deg": _deg(weather)}
        if _measure([("side", g.SIDE_W - 0.15, "side", side)])["side"] <= g.FRONT_TOP_H - 0.02:
            return side
        if n_tomorrow:
            n_tomorrow -= 1
        elif outlook:
            outlook = outlook[:-1]
        elif n_events > 1:
            n_events -= 1
        else:
            return side


def _block(p: Piece) -> dict:
    return {"key": p.key, "type": "brief" if p.kind == "brief" else "story",
            "section": p.section, "piece": _pd(p)}


def _inside(pool: list[Piece], also: list[FeedItem], reserves: list[Piece],
            max_pages: int, first_page_offset: float = 0.0) -> tuple[list[list[list[dict]]], set[str], list[str]]:
    blocks = [_block(p) for p in pool]
    also_block = ({"key": "also", "type": "also", "section": None,
                   "items": [i.model_dump() for i in also]} if also else None)
    rblocks = [_block(p) for p in reserves]
    every = blocks + rblocks + ([also_block] if also_block else [])
    measured = _measure_blocks([(b["key"], g.INSIDE_COL_W, "block", b) for b in every])
    heads = _measure([("head:" + b["key"], g.INSIDE_COL_W, "block_head", b) for b in every])

    def to_block(b: dict) -> Block:
        m = measured[b["key"]]
        if b["type"] == "also":
            return Block(b["key"], m.height, heads["head:" + b["key"]], m.lines, min_after=2)
        return Block(b["key"], m.height, heads["head:" + b["key"]], m.lines, para_ends=m.para_ends)

    blocks_by_key = {b["key"]: to_block(b) for b in every}
    pages, dropped = plan_inside([blocks_by_key[b["key"]] for b in blocks], [blocks_by_key[b["key"]] for b in rblocks],
                                 g.INSIDE_H, g.INSIDE_COLS, max_pages, g.GAP, fold_at=g.INSIDE_FOLD_AT,
                                 first_page_offset=first_page_offset,
                                 also=blocks_by_key["also"] if also_block else None)
    lookup = {b["key"]: b for b in every}

    def placed(k, top, win, kept):
        b = dict(lookup[k], top=round(top, 3))
        height = measured[k].height
        if kept is not None:                 # cut short at a paragraph end: print only the kept paragraphs
            b["piece"] = dict(b["piece"], paragraphs=b["piece"]["paragraphs"][:kept])
            height = next(t.height for t in blocks_by_key[k].trims() if t.kept == kept)
        if win != (0.0, None):
            y0, y1 = win
            b["win"] = {"y0": round(y0, 4), "h": round((height if y1 is None else y1) - y0, 4)}
        if b["type"] == "also":              # how many of the headlines this part shows
            lo, hi = win[0], float("inf") if win[1] is None else win[1]
            b["n_items"] = sum(1 for top, bottom in blocks_by_key[k].units if top >= lo - 1e-6 and bottom <= hi + 1e-6)
        return b

    view = [[[placed(k, top, win, col.kept[i] if i < len(col.kept) else None)
              for i, (k, top, win) in enumerate(zip(col.keys, col.tops, col.windows))] for col in page]
            for page in pages]
    placed_keys = {k for page in pages for col in page for k in col.keys}
    return view, placed_keys, [k for k in dropped if k != "also"]


def _index(sections: list[str], pages: list, has_good: bool, back_page: int,
           puzzle: PuzzleView | None, weather_band: bool = False) -> list[tuple[str, int]]:
    out = [("Weather", 2)] if weather_band else []
    for name in sections:
        for i, page in enumerate(pages):
            if any(b.get("section") == name for col in page for b in col):
                out.append((name, i + 2))
                break
    if has_good:
        out.append(("Good News", back_page))
    if puzzle is not None:
        out.append((puzzle.kind.title(), back_page))
    return out


def _crossword_view(fill: CrosswordFill, clues: dict[tuple[int, str], str]) -> dict:
    numbers = fill.numbers()
    cells = [[{"block": ch == "#", "number": numbers.get((r, c))} for c, ch in enumerate(row)]
             for r, row in enumerate(fill.grid)]

    def listing(direction: str) -> list[tuple[int, str]]:
        return sorted((s.number, clues[(s.number, direction)]) for s in fill.slots if s.direction == direction)

    return {"cells": cells, "across": listing("across"), "down": listing("down")}


def _answers_view(answers: dict | None) -> dict | None:
    if not answers:
        return None
    if answers.get("kind") == "sudoku":
        return {"kind": "sudoku", "rows": [[str(v) for v in row] for row in answers["solution"]]}
    if answers.get("kind") == "crossword":
        return {"kind": "crossword", "rows": [list(row) for row in answers["grid"]]}
    return None


def _date_label(day: date) -> str:
    return f"{day:%A}, {day:%B} {day.day}, {day.year}"


def _front_plan(inp: EditionInput, strip: bool) -> dict:
    """Everything the front page and the pages behind it depend on, fitted with or without the index strip."""
    s = inp.settings
    bottom_h = g.front_bottom_h(strip)
    stories = [p for p in inp.primary if p.kind != "brief"]
    front_briefs = _fit_front_briefs([p for p in inp.primary if p.kind == "brief"][:6], bottom_h)
    # the right-hand column below the fold: briefs if there are any, else the also-in-the-news headlines
    # (they leave the inside pages), else nothing, and the second story then spans the whole page
    front_also = [] if front_briefs else inp.also[:_fit_also_side(inp.also, bottom_h)]
    front = _fit_front(inp.lead, inp.lead_image, inp.work_dir, s.halftone_gamma,
                       stories[0] if stories else None, bottom_h, wide=not front_briefs and not front_also)
    lead_view, story2, story2_piece = front["lead"], front["story2"], front["story2_piece"]
    used = {p.key for p in front_briefs} | ({story2_piece.key} if story2_piece else set())
    pool = [p for p in inp.primary if p.key not in used]

    # the expanded Weather band needs an inside page to sit on, so it prints only when one is allowed
    weather_band = bool(s.expanded_weather and inp.weather and inp.weather.details and s.max_sheets >= 2)
    inside_pages, inside_keys, dropped = _inside(
        pool, inp.also[len(front_also):], inp.reserves, s.max_sheets * 2 - 2,
        first_page_offset=g.WEATHER_BAND_H + g.REGION_GAP if weather_band else 0.0)
    good = _fit_good(inp.good_news) if inp.good_news else None
    inside_also = sum(b.get("n_items", 0) for page in inside_pages for col in page for b in col if b["key"] == "also")
    back_page = 2 + len(inside_pages)

    placed = ([inp.lead] if inp.lead else []) + ([story2_piece] if story2_piece else []) + front_briefs
    placed += [p for p in pool + inp.reserves if p.key in inside_keys]
    placed += [inp.good_news] if inp.good_news else []

    index = _index(s.sections, inside_pages, good is not None, back_page, inp.puzzle, weather_band)
    return {"bottom_h": bottom_h, "lead_view": lead_view, "story2": story2, "front_briefs": front_briefs,
            "front_also": front_also, "inside_also": inside_also, "wide": not front_briefs and not front_also,
            "inside_pages": inside_pages, "inside_keys": inside_keys, "dropped": dropped, "good": good,
            "back_page": back_page, "placed": placed, "index": index, "weather_band": weather_band}


def render_edition(inp: EditionInput) -> RenderResult:
    s = inp.settings
    # the index strip costs the below-fold regions some height, so fit with it first and give the
    # height back only when there turns out to be no index to print
    plan = _front_plan(inp, True)
    if not plan["index"]:
        bare = _front_plan(inp, False)
        if not bare["index"]:
            plan = bare
    lead_view, story2, front_briefs = plan["lead_view"], plan["story2"], plan["front_briefs"]
    inside_pages, inside_keys, dropped = plan["inside_pages"], plan["inside_keys"], plan["dropped"]
    good, back_page, placed, index = plan["good"], plan["back_page"], plan["placed"], plan["index"]
    weather_band, puzzle = plan["weather_band"], inp.puzzle
    index_size = _fit_size("", "Libre Franklin", 500, INDEX_STRIP_SIZES, g.CONTENT_W, index) if index else 0
    ear_temp, ear_temp_size = "", EAR_TEMP_SIZES[0]
    if inp.weather:
        deg = _deg(inp.weather)
        ear_temp = Markup("{}{} / {}{}").format(inp.weather.today.high, deg, inp.weather.today.low, deg)
        ear_inside = g.EAR_W - 2 * (g.EAR_PAD_X + g.EAR_BORDER) - EAR_ICON_W
        ear_temp_size = _fit_size(ear_temp, "Libre Franklin", 700, EAR_TEMP_SIZES, ear_inside)
    html = _env.get_template("edition.html.j2").render(
        **_common(s),
        nameplate_size=_nameplate_size(s, g.CONTENT_W - 2 * g.EAR_W - 2 * g.NAMEPLATE_GAP),
        ear_temp=ear_temp, ear_temp_size=ear_temp_size,
        date_label=_date_label(inp.day), number=inp.number, weather=inp.weather, deg=_deg(inp.weather),
        side=_fit_side(inp.events, inp.weather, inp.tomorrow_events),
        bottom_h=plan["bottom_h"], index=index, index_size=index_size,
        lead=lead_view, story2=story2, front_briefs=[_pd(p) for p in front_briefs],
        front_also=[i.model_dump() for i in plan["front_also"]],
        inside_pages=inside_pages,
        good=good, otd=inp.on_this_day, puzzle=puzzle,
        puzzle_name=puzzle.kind.title() if puzzle else "Puzzle",
        xword=_crossword_view(puzzle.crossword, puzzle.clues) if puzzle and puzzle.kind == "crossword" else None,
        answers=_answers_view(inp.answers), back_page=back_page,
        weather_band=weather_band, band_offset=g.WEATHER_BAND_H + g.REGION_GAP,
    )
    doc = weasyprint.HTML(string=html, base_url=str(inp.work_dir)).render()
    return RenderResult(html=html, pdf=doc.write_pdf(), pages=len(doc.pages), placed=placed, dropped=dropped,
                        also=inp.also[:len(plan["front_also"]) + plan["inside_also"]])


def render_test_page(settings: Settings, when: str) -> bytes:
    html = _env.get_template("testpage.html.j2").render(
        **_common(settings), when=when, nameplate_size=_nameplate_size(settings, g.CONTENT_W - 2 * g.NAMEPLATE_GAP))
    return weasyprint.HTML(string=html, base_url=str(HERE)).write_pdf()
