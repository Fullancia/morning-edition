import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .models import Piece
from .settings import Settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feeds (
  id INTEGER PRIMARY KEY,
  url TEXT NOT NULL UNIQUE,
  section TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  headlines_only INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS editions (
  id INTEGER PRIMARY KEY,
  number INTEGER NOT NULL UNIQUE,
  date TEXT NOT NULL,
  status TEXT NOT NULL,
  pdf_path TEXT,
  puzzle_type TEXT,
  puzzle_solution TEXT,
  log TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  is_preview INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS stories (
  id INTEGER PRIMARY KEY,
  edition_id INTEGER NOT NULL REFERENCES editions(id),
  url TEXT NOT NULL,
  section TEXT NOT NULL,
  kind TEXT NOT NULL,
  rank INTEGER NOT NULL,
  title TEXT NOT NULL,
  word_count INTEGER NOT NULL,
  image_used INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS stories_url ON stories(url);
"""


@dataclass
class Feed:
    id: int
    url: str
    section: str
    enabled: bool
    headlines_only: bool


@dataclass
class Edition:
    id: int
    number: int
    date: str
    status: str
    pdf_path: str | None
    puzzle_type: str | None
    puzzle_solution: str | None
    log: list[dict]
    created_at: str
    is_preview: bool = False


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    columns = {r["name"] for r in conn.execute("PRAGMA table_info(editions)")}
    if "is_preview" not in columns:
        conn.execute("ALTER TABLE editions ADD COLUMN is_preview INTEGER NOT NULL DEFAULT 0")
        conn.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_settings(conn: sqlite3.Connection) -> Settings:
    row = conn.execute("SELECT data FROM settings WHERE id = 1").fetchone()
    return Settings.model_validate_json(row["data"]) if row else Settings()


def save_settings(conn: sqlite3.Connection, s: Settings) -> None:
    conn.execute(
        "INSERT INTO settings (id, data) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET data = excluded.data",
        (s.model_dump_json(),),
    )
    conn.commit()


def _feed(row: sqlite3.Row) -> Feed:
    return Feed(row["id"], row["url"], row["section"], bool(row["enabled"]), bool(row["headlines_only"]))


def list_feeds(conn: sqlite3.Connection, enabled_only: bool = False) -> list[Feed]:
    sql = "SELECT * FROM feeds" + (" WHERE enabled = 1" if enabled_only else "") + " ORDER BY id"
    return [_feed(r) for r in conn.execute(sql)]


def add_feed(conn: sqlite3.Connection, url: str, section: str, headlines_only: bool = False) -> int:
    cur = conn.execute(
        "INSERT INTO feeds (url, section, headlines_only) VALUES (?, ?, ?)",
        (url.strip(), section, int(headlines_only)),
    )
    conn.commit()
    return cur.lastrowid


def set_feed_enabled(conn: sqlite3.Connection, feed_id: int, enabled: bool) -> None:
    conn.execute("UPDATE feeds SET enabled = ? WHERE id = ?", (int(enabled), feed_id))
    conn.commit()


def delete_feed(conn: sqlite3.Connection, feed_id: int) -> None:
    conn.execute("DELETE FROM feeds WHERE id = ?", (feed_id,))
    conn.commit()


def _edition(row: sqlite3.Row) -> Edition:
    return Edition(
        row["id"], row["number"], row["date"], row["status"], row["pdf_path"],
        row["puzzle_type"], row["puzzle_solution"], json.loads(row["log"]), row["created_at"],
        bool(row["is_preview"]),
    )


def create_edition(conn: sqlite3.Connection, date_iso: str, preview: bool = False) -> Edition:
    number = conn.execute("SELECT COALESCE(MAX(number), 0) + 1 FROM editions").fetchone()[0]
    cur = conn.execute(
        "INSERT INTO editions (number, date, status, created_at, is_preview) VALUES (?, ?, 'building', ?, ?)",
        (number, date_iso, _now(), int(preview)),
    )
    conn.commit()
    return get_edition(conn, cur.lastrowid)


def get_edition(conn: sqlite3.Connection, edition_id: int) -> Edition | None:
    row = conn.execute("SELECT * FROM editions WHERE id = ?", (edition_id,)).fetchone()
    return _edition(row) if row else None


def get_edition_by_number(conn: sqlite3.Connection, number: int) -> Edition | None:
    row = conn.execute("SELECT * FROM editions WHERE number = ?", (number,)).fetchone()
    return _edition(row) if row else None


def previous_answers_edition(conn: sqlite3.Connection, number: int) -> Edition | None:
    """The latest non-preview edition before `number` that has puzzle answers to print."""
    row = conn.execute(
        "SELECT * FROM editions WHERE is_preview = 0 AND number < ? AND puzzle_solution IS NOT NULL "
        "ORDER BY number DESC LIMIT 1", (number,)).fetchone()
    return _edition(row) if row else None


def next_daily_number(conn: sqlite3.Connection, exclude_id: int) -> int:
    """How many non-preview editions there will be once this one counts: drives puzzle alternation."""
    return conn.execute("SELECT COUNT(*) FROM editions WHERE is_preview = 0 AND id != ?",
                        (exclude_id,)).fetchone()[0] + 1


def list_editions(conn: sqlite3.Connection, limit: int = 60) -> list[Edition]:
    rows = conn.execute("SELECT * FROM editions ORDER BY number DESC LIMIT ?", (limit,))
    return [_edition(r) for r in rows]


_EDITABLE = {"status", "pdf_path", "puzzle_type", "puzzle_solution", "is_preview"}


def update_edition(conn: sqlite3.Connection, edition_id: int, **fields) -> None:
    bad = set(fields) - _EDITABLE
    if bad:
        raise ValueError(f"not editable: {bad}")
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE editions SET {cols} WHERE id = ?", (*fields.values(), edition_id))
    conn.commit()


def append_log(conn: sqlite3.Connection, edition_id: int, step: str, status: str, message: str) -> None:
    row = conn.execute("SELECT log FROM editions WHERE id = ?", (edition_id,)).fetchone()
    log = json.loads(row["log"])
    log.append({"at": _now(), "step": step, "status": status, "message": message})
    conn.execute("UPDATE editions SET log = ? WHERE id = ?", (json.dumps(log), edition_id))
    conn.commit()


def record_stories(conn: sqlite3.Connection, edition_id: int, pieces: list[Piece], ranks: dict[str, int]) -> None:
    conn.executemany(
        "INSERT INTO stories (edition_id, url, section, kind, rank, title, word_count, image_used) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (edition_id, p.key, p.section, p.kind, ranks.get(p.key, 0), p.headline,
             sum(len(x.split()) for x in p.paragraphs), int(p.image_path is not None))
            for p in pieces
        ],
    )
    conn.commit()


def list_stories(conn: sqlite3.Connection, edition_id: int) -> list[sqlite3.Row]:
    """An edition's recorded stories in print order."""
    return conn.execute("SELECT * FROM stories WHERE edition_id = ? ORDER BY id", (edition_id,)).fetchall()


def seen_keys(conn: sqlite3.Connection) -> set[str]:
    return {r["url"] for r in conn.execute("SELECT DISTINCT url FROM stories")}
