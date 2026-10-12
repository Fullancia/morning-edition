"""The "Today's paper" list: what the latest edition printed, with a link back to each original."""
import json
from pathlib import Path
from urllib.parse import urlsplit

from .. import db
from ..settings import GOOD_NEWS

ALSO = "Also in the news"


def is_web_url(url: str) -> bool:
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False
    return parts.scheme.lower() in ("http", "https") and bool(parts.netloc)


def _entry(headline: str, source: str, url: str) -> dict:
    url = str(url or "").strip()
    return {"headline": str(headline), "source": str(source or ""), "href": url if is_web_url(url) else None}


def _load_placed(work: Path) -> dict | None:
    try:
        data = json.loads((work / "placed.json").read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _from_placed(data: dict) -> tuple[list[tuple[str, dict]], list[dict]]:
    pieces = []
    for p in data.get("pieces") or []:
        if not isinstance(p, dict):
            continue
        slot = (GOOD_NEWS if p.get("good_news") or p.get("kind") == "good_news"
                else str(p.get("section") or "").strip() or "Other")
        # placed.json from before urls were stored: the normalized key is still a working link
        url = p["url"] if "url" in p else p.get("key", "")
        pieces.append((slot, p.get("kind", ""), _entry(p.get("headline", ""), p.get("source", ""), url)))
    also = [_entry(i.get("title", ""), i.get("source", ""), i.get("url", ""))
            for i in data.get("also") or [] if isinstance(i, dict)]
    return pieces, also


def _from_stories(conn, edition_id: int) -> tuple[list, list[dict]]:
    """Editions built before placed.json carried urls: the stories table, whose stored key is the link."""
    rows = db.list_stories(conn, edition_id)
    return [(GOOD_NEWS if r["kind"] == "good_news" else r["section"], r["kind"],
             _entry(r["title"], "", r["url"])) for r in rows], []


def todays_paper(conn, ed: db.Edition | None, data_dir: Path, sections: list[str]) -> list[dict]:
    """Groups of {name, entries} in the paper's section order: each section's lead first, then
    Good News, then Also in the news. Empty when nothing is known about the edition."""
    if ed is None:
        return []
    data = _load_placed(data_dir / "editions" / f"{ed.number:04d}")
    pieces, also = _from_placed(data) if data and "pieces" in data else _from_stories(conn, ed.id)
    groups: dict[str, list[dict]] = {name: [] for name in sections}
    groups[GOOD_NEWS] = []
    for name, kind, entry in pieces:   # print order; a section dropped from Settings still shows up
        bucket = groups.setdefault(name, [])
        if kind == "lead":
            bucket.insert(0, entry)
        else:
            bucket.append(entry)
    if also:
        groups[ALSO] = also
    ordered = [n for n in groups if n not in (GOOD_NEWS, ALSO)] + [GOOD_NEWS, ALSO]
    return [{"name": n, "entries": groups[n]} for n in ordered if groups.get(n)]
