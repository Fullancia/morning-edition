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
from .measure import heights, widths
from .pack import Block, fit_count, pack, plan_inside

HERE = Path(__file__).parent
TEMPLATES = HERE / "templates"
FONTS = HERE / "fonts"
MASTHEAD_WEIGHTS = {"Pirata One": 400, "Manufacturing Consent": 400, "Grenze Gotisch": 700,
                    "Playfair Display": 900}
LEAD_SIZES = (28, 24, 20)
NAMEPLATE_SIZES = (60, 54, 48, 42, 36, 30, 24, 20, 16, 12)
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


def _fit_size(text: str, font: str, weight: int, sizes: tuple[int, ...], avail: float) -> int:
    """The largest size (pt) at which text sits on one line in avail inches; the smallest if none does."""
    probes = [{"key": f"s{sz}", "text": text, "font": font, "weight": weight, "size": sz} for sz in sizes]
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


RAGGED_GAP = 3 / 72     # .ragged p margin-bottom in edition.css


def _paragraph_heights(paragraphs: list[str], width: float, macro: str = "para") -> list[float]:
    hs = _measure([(f"p{i}", width, macro, t) for i, t in enumerate(paragraphs)])
    return [hs[f"p{i}"] for i in range(len(paragraphs))]


def _fit_lead(lead: Piece, image: Path | None, work_dir: Path, gamma: float) -> dict:
    head, head_h = None, 0.0
    for size in LEAD_SIZES:
        head = _pd(lead, size=size)
        head_h = _measure([("h", g.LEAD_W, "lead_header", head)])["h"]
        if head_h <= g.LEAD_HEADER_MAX:
            break
    else:
        head = _pd(lead, size=LEAD_SIZES[-1], deck="")
        head_h = _measure([("h", g.LEAD_W, "lead_header", head)])["h"]
    text_h = g.FRONT_TOP_H - head_h - 0.08
    photo = None
    photo_h = min(g.PHOTO_MAX_H, text_h - g.CAPTION_H)
    if image is not None and photo_h >= g.PHOTO_MIN_H:
        out = work_dir / "lead-halftone.png"
        try:
            halftone(image, out, g.PHOTO_W, photo_h, gamma=gamma)
            photo = {"src": out.as_uri(), "height": round(photo_h, 3), "caption": f"Photo: {lead.source}"}
        except OSError:
            photo = None
    ncols = 2 if photo else 3
    col_w = g.LEAD_TEXT_W_PHOTO if photo else g.LEAD_TEXT_W_NOPHOTO
    k = max(1, fit_count(_paragraph_heights(lead.paragraphs, col_w), ncols * (text_h - g.LINE)))
    rest = lead.paragraphs[k:]
    cont: list[str] = []
    if rest:
        k2 = fit_count(_paragraph_heights(rest, g.CONT_TEXT_W, "para_ragged"), g.FRONT_BOTTOM_H - g.KICKER_H,
                       gap=RAGGED_GAP)
        cont = rest[:k2]
    return {"head": head, "text_h": round(text_h, 3), "ncols": ncols, "photo": photo,
            "top": lead.paragraphs[:k], "cont": cont}


def _fit_columns(p: Piece, header_macro: str, header_w: float, col_w: float, ncols: int,
                 height: float) -> dict:
    head = _pd(p)
    head_h = _measure([("h", header_w, header_macro, head)])["h"]
    text_h = height - head_h - 0.05
    k = max(1, fit_count(_paragraph_heights(p.paragraphs, col_w), ncols * (text_h - g.LINE)))
    return {"head": head, "paras": p.paragraphs[:k], "text_h": round(text_h, 3)}


def _fit_front_briefs(briefs: list[Piece]) -> list[Piece]:
    if not briefs:
        return []
    hs = _measure([(f"b{i}", g.BRIEFS_TEXT_W, "brief", _pd(p)) for i, p in enumerate(briefs)])
    cols, _ = pack([Block(str(i), hs[f"b{i}"]) for i in range(len(briefs))],
                   g.FRONT_BOTTOM_H - g.KICKER_H, 1, g.GAP)
    chosen = {int(k) for c in cols for k in c.keys}
    return [p for i, p in enumerate(briefs) if i in chosen]


def _fit_side(events: list[Event], weather: Weather | None, index: list[tuple[str, int]]) -> dict:
    """Trim the front-page sidebar until it fits: outlook days first, then events."""
    outlook = list(weather.outlook[:MAX_OUTLOOK]) if weather else []
    n_events = min(len(events), MAX_EVENTS)
    while True:
        side = {"events": events[:n_events], "more": len(events) - n_events, "index": index,
                "outlook": outlook, "deg": _deg(weather)}
        if _measure([("side", g.SIDE_W - 0.15, "side", side)])["side"] <= g.FRONT_TOP_H - 0.02:
            return side
        if outlook:
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
    if also:
        blocks.append({"key": "also", "type": "also", "section": None,
                       "items": [i.model_dump() for i in also]})
    rblocks = [_block(p) for p in reserves]
    every = blocks + rblocks
    hs = _measure([(b["key"], g.INSIDE_COL_W, "block", b) for b in every]
                  + [("head:" + b["key"], g.INSIDE_COL_W, "block_head", b) for b in every])

    def to_block(b: dict) -> Block:
        return Block(b["key"], hs[b["key"]], hs["head:" + b["key"]])

    pages, dropped = plan_inside([to_block(b) for b in blocks], [to_block(b) for b in rblocks],
                                 g.INSIDE_H, g.INSIDE_COLS, max_pages, g.GAP, fold_at=g.INSIDE_FOLD_AT,
                                 first_page_offset=first_page_offset)
    lookup = {b["key"]: b for b in every}
    view = [[[dict(lookup[k], top=round(top, 3)) for k, top in zip(col.keys, col.tops)] for col in page]
            for page in pages]
    placed = {k for page in pages for col in page for k in col.keys}
    return view, placed, [k for k in dropped if k != "also"]


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


def render_edition(inp: EditionInput) -> RenderResult:
    s = inp.settings
    lead_view = _fit_lead(inp.lead, inp.lead_image, inp.work_dir, s.halftone_gamma) if inp.lead else None

    stories = [p for p in inp.primary if p.kind != "brief"]
    story2_piece = stories[0] if stories else None
    story2 = (_fit_columns(story2_piece, "story_header", g.STORY2_W, g.STORY2_COL_W, 2, g.FRONT_BOTTOM_H)
              if story2_piece else None)
    front_briefs = _fit_front_briefs([p for p in inp.primary if p.kind == "brief"][:6])
    used = {p.key for p in front_briefs} | ({story2_piece.key} if story2_piece else set())
    pool = [p for p in inp.primary if p.key not in used]

    # the expanded Weather band needs an inside page to sit on, so it prints only when one is allowed
    weather_band = bool(s.expanded_weather and inp.weather and inp.weather.details and s.max_sheets >= 2)
    inside_pages, inside_keys, dropped = _inside(
        pool, inp.also, inp.reserves, s.max_sheets * 2 - 2,
        first_page_offset=g.WEATHER_BAND_H + g.REGION_GAP if weather_band else 0.0)
    good = (_fit_columns(inp.good_news, "good_header", g.GOOD_W, g.GOOD_COL_W, 3, g.BACK_TOP_H)
            if inp.good_news else None)
    back_page = 2 + len(inside_pages)

    placed = ([inp.lead] if inp.lead else []) + ([story2_piece] if story2_piece else []) + front_briefs
    placed += [p for p in pool + inp.reserves if p.key in inside_keys]
    placed += [inp.good_news] if inp.good_news else []

    puzzle = inp.puzzle
    index = _index(s.sections, inside_pages, good is not None, back_page, puzzle, weather_band)
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
        side=_fit_side(inp.events, inp.weather, index),
        lead=lead_view, story2=story2, front_briefs=[_pd(p) for p in front_briefs],
        inside_pages=inside_pages,
        good=good, otd=inp.on_this_day, puzzle=puzzle,
        puzzle_name=puzzle.kind.title() if puzzle else "Puzzle",
        xword=_crossword_view(puzzle.crossword, puzzle.clues) if puzzle and puzzle.kind == "crossword" else None,
        answers=_answers_view(inp.answers), back_page=back_page,
        weather_band=weather_band, band_offset=g.WEATHER_BAND_H + g.REGION_GAP,
    )
    doc = weasyprint.HTML(string=html, base_url=str(inp.work_dir)).render()
    return RenderResult(html=html, pdf=doc.write_pdf(), pages=len(doc.pages), placed=placed, dropped=dropped,
                        also=inp.also if "also" in inside_keys else [])


def render_test_page(settings: Settings, when: str) -> bytes:
    html = _env.get_template("testpage.html.j2").render(
        **_common(settings), when=when, nameplate_size=_nameplate_size(settings, g.CONTENT_W - 2 * g.NAMEPLATE_GAP))
    return weasyprint.HTML(string=html, base_url=str(HERE)).write_pdf()
