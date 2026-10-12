import json
from typing import Literal

from pydantic import BaseModel

from ..models import FeedItem, Piece
from ..settings import GOOD_NEWS
from ..textutil import no_em_dash
from .client import LLM, LLMError

WORDS = {"lead": 450, "story": 250, "brief": 150, "good_news": 350}


class Pick(BaseModel):
    id: int
    length: Literal["story", "brief"]
    section: str | None = None  # None keeps the item's feed section


class Selection(BaseModel):
    lead_id: int | None
    good_news_id: int | None
    primary: list[Pick]
    reserves: list[Pick]
    also: list[int]


class Condensed(BaseModel):
    headline: str
    deck: str
    paragraphs: list[str]


class Clue(BaseModel):
    number: int
    direction: Literal["across", "down"]
    clue: str


class ClueSet(BaseModel):
    clues: list[Clue]


SELECT_SYSTEM = """You are the editor of a small personal newspaper that is printed on paper every \
morning for one reader. You choose what goes in today's edition from a list of candidate stories \
gathered from the reader's RSS feeds in the last 24 hours.

Rules:
- Pick one lead story: the most important or most interesting story for this reader. It must not be \
from the "Good News" section and must not be marked headlines_only.
- Pick one Good News story: genuinely uplifting news about people, communities, nature, or progress. \
A good local story beats a national one. Reject product pitches, health or medical advice, \
sponsored content, and listicles dressed up as good news. It may come from any section. Use null if \
nothing qualifies.
- primary: the stories today's news warrants, ranked best first, each with a length of "story" \
(about 250 words) or "brief" (about 150 words). Never include the lead or the Good News pick.
- reserves: more good stories, ranked, to use only if there is room on the page.
- also: ids of headlines_only items worth listing as a one-line headline.
- Leave out sponsored posts, advertisements, real estate listings, event promotions, and \
duplicates. When several items cover the same story, keep the best source.
- Stories picked for the reader's local section (the first section listed, normally "Local") must \
actually be about the reader's home area: the town, the county, or the nearby region. An item that came \
from a local feed but is not about the area is either dropped or, if it is real news about somewhere \
else, placed in the section where it belongs by setting the pick's "section" to that section's name. \
Otherwise set "section" to null to keep the feed's section.
- Local outlets publish paid filler that looks like news: wellness, finance, marketing, \
legal-explainer, and product articles, "how X can help" pieces, and "best X for Y" lists. Treat these \
as sponsored content and leave them out.
- Only use ids from the candidate list."""

CONDENSE_SYSTEM = """You condense news articles for a printed personal newspaper. Write in clear, \
plain newspaper style in the inverted pyramid: the most important facts first, so the end can be \
cut without losing anything essential. Use only facts stated in the source text. Never add facts, \
quotes, names, or numbers that are not in it. Keep paragraphs short, two or three sentences each. \
Never use em dashes. No markdown. The headline is at most 10 words; the deck is one sentence."""

CLUE_SYSTEM = """You write crossword clues for a friendly daily newspaper puzzle. Write one fair clue \
per answer, under 30 characters when possible, never containing the answer itself, avoiding obscure \
trivia. Answers that are abbreviations or partial phrases should be clued that way. Never use em dashes."""


def _items_by_id(items: list[FeedItem]) -> dict[int, FeedItem]:
    return {i.id: i for i in items}


def _eligible_story(item: FeedItem | None) -> bool:
    return item is not None and not item.headlines_only and item.section != GOOD_NEWS


def sanitize(sel: Selection, items: list[FeedItem], sections: list[str] | None = None) -> Selection:
    by_id = _items_by_id(items)
    lead = sel.lead_id if _eligible_story(by_id.get(sel.lead_id)) else None
    good = sel.good_news_id
    if good is not None and (good not in by_id or by_id[good].headlines_only or good == lead):
        good = None
    used = {x for x in (lead, good) if x is not None}

    def clean(picks: list[Pick]) -> list[Pick]:
        out = []
        for p in picks:
            if p.id in used or not _eligible_story(by_id.get(p.id)):
                continue
            used.add(p.id)
            out.append(p if p.section is None or p.section in (sections or [])
                       else p.model_copy(update={"section": None}))
        return out

    primary = clean(sel.primary)
    reserves = clean(sel.reserves)
    if lead is None and primary:
        lead = primary.pop(0).id
    also = [i for i in dict.fromkeys(sel.also) if i in by_id and by_id[i].headlines_only]
    return Selection(lead_id=lead, good_news_id=good, primary=primary, reserves=reserves, also=also)


def fallback_selection(items: list[FeedItem], sections: list[str]) -> Selection:
    def newest(pool):
        return sorted(pool, key=lambda i: (i.published is None, -(i.published.timestamp() if i.published else 0), i.id))

    queues = [newest([i for i in items if i.section == s and not i.headlines_only]) for s in sections]
    order: list[FeedItem] = []
    while any(queues):
        for q in queues:
            if q:
                order.append(q.pop(0))
    good = newest([i for i in items if i.section == GOOD_NEWS and not i.headlines_only])
    picks = [Pick(id=i.id, length="story") for i in order]
    sel = Selection(
        lead_id=order[0].id if order else None,
        good_news_id=good[0].id if good else None,
        primary=picks[1:9], reserves=picks[9:15],
        also=[i.id for i in newest([i for i in items if i.headlines_only])][:6],
    )
    return sanitize(sel, items, sections)


def select(llm: LLM | None, items: list[FeedItem], sections: list[str], interests: str,
           inside_pages: int, max_inside_pages: int, home: str = "") -> Selection:
    if llm is None or not items:
        return fallback_selection(items, sections)
    candidates = [
        {"id": i.id, "section": i.section, "source": i.source, "title": i.title,
         "summary": i.summary, "published": i.published.isoformat() if i.published else None,
         "headlines_only": i.headlines_only}
        for i in items
    ]
    user = (
        f"Reader's home: {home or 'not set'}\n"
        f"Reader's interests: {interests or 'general news'}\n"
        f"Sections in page order: {', '.join(sections)}\n"
        f"Space: a normal day fills {inside_pages} inside pages, which holds about "
        f"{3 * inside_pages} stories plus {3 * inside_pages} briefs. A heavy news day may use up to "
        f"{max_inside_pages} inside pages. Size the primary list to what today's news warrants, "
        f"and give up to {3 * inside_pages} reserves.\n\n"
        f"Candidates:\n{json.dumps(candidates, ensure_ascii=False)}"
    )
    try:
        sel = llm.ask_json(SELECT_SYSTEM, user, Selection)
    except LLMError:
        return fallback_selection(items, sections)
    sel = sanitize(sel, items, sections)
    # If sanitization left us with no lead and empty primary, but eligible stories exist, fall back
    if sel.lead_id is None and not sel.primary:
        eligible = [i for i in items if _eligible_story(i)]
        if eligible:
            return fallback_selection(items, sections)
    return sel


def _teaser_piece(item: FeedItem) -> Piece:
    return Piece(item_id=item.id, section=item.section, kind="brief", headline=no_em_dash(item.title),
                 deck="", paragraphs=[no_em_dash(item.summary or item.title)], source=item.source,
                 key=item.key, url=item.url)


def condense(llm: LLM | None, item: FeedItem, article_text: str | None, kind: str) -> Piece:
    if llm is None or not article_text:
        return _teaser_piece(item)
    words = WORDS[kind]
    user = (f"Write a {kind.replace('_', ' ')} of about {words} words.\n"
            f"Source publication: {item.source}\nOriginal headline: {item.title}\n\n"
            f"Article:\n{article_text}")
    try:
        out = llm.ask_json(CONDENSE_SYSTEM, user, Condensed)
    except LLMError:
        return _teaser_piece(item)
    paragraphs = [no_em_dash(p.strip()) for p in out.paragraphs if p.strip()]
    if not paragraphs:
        return _teaser_piece(item)
    return Piece(item_id=item.id, section=item.section, kind=kind, headline=no_em_dash(out.headline.strip()),
                 deck=no_em_dash(out.deck.strip()), paragraphs=paragraphs, source=item.source, key=item.key,
                 url=item.url)


def write_clues(llm: LLM, entries: list[tuple[int, str, str]]) -> dict[tuple[int, str], str]:
    if not entries:
        return {}
    listing = "\n".join(f"{n} {d.title()}: {a} ({len(a)})" for n, d, a in entries)
    out = llm.ask_json(CLUE_SYSTEM, f"Write a clue for each entry:\n{listing}", ClueSet)
    clues = {(c.number, c.direction): no_em_dash(c.clue.strip()) for c in out.clues if c.clue.strip()}
    missing = [(n, d) for n, d, _ in entries if (n, d) not in clues]
    if missing:
        raise LLMError(f"missing clues for {missing}")
    return {(n, d): clues[(n, d)] for n, d, _ in entries}
