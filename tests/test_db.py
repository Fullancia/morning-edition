import json

from morning_edition import db
from morning_edition.models import Piece
from morning_edition.settings import Settings


def test_settings_roundtrip(conn):
    assert db.load_settings(conn) == Settings()
    s = Settings(paper_name="The Rick Times", print_time="05:30")
    db.save_settings(conn, s)
    assert db.load_settings(conn).paper_name == "The Rick Times"


def test_feed_crud(conn):
    fid = db.add_feed(conn, "https://example.com/feed", "Local")
    db.add_feed(conn, "https://news.google.com/rss/search?q=x", "Local", headlines_only=True)
    feeds = db.list_feeds(conn)
    assert [f.url for f in feeds][0] == "https://example.com/feed"
    assert feeds[1].headlines_only is True
    db.set_feed_enabled(conn, fid, False)
    assert len(db.list_feeds(conn, enabled_only=True)) == 1
    db.delete_feed(conn, fid)
    assert len(db.list_feeds(conn)) == 1


def test_edition_numbers_increment_and_log(conn):
    e1 = db.create_edition(conn, "2026-10-12")
    e2 = db.create_edition(conn, "2026-10-13")
    assert (e1.number, e2.number) == (1, 2)
    assert e1.status == "building"
    db.append_log(conn, e2.id, "gather", "ok", "12 items")
    db.update_edition(conn, e2.id, status="built", pdf_path="/x.pdf")
    got = db.get_edition(conn, e2.id)
    assert got.status == "built" and got.pdf_path == "/x.pdf"
    assert got.log[0]["step"] == "gather" and got.log[0]["status"] == "ok"
    assert db.get_edition_by_number(conn, 1).id == e1.id
    assert [e.number for e in db.list_editions(conn)] == [2, 1]


def test_story_history(conn):
    e = db.create_edition(conn, "2026-10-12")
    p = Piece(item_id=1, section="Local", kind="story", headline="H", deck="",
              paragraphs=["one two three"], source="Example Local News", key="https://news.example.com/a")
    db.record_stories(conn, e.id, [p], {"https://news.example.com/a": 1})
    assert db.seen_keys(conn) == {"https://news.example.com/a"}


def test_preview_flag_lookups_and_migration(tmp_path):
    import sqlite3

    old = tmp_path / "old.sqlite"
    raw = sqlite3.connect(old)
    raw.executescript("""
        CREATE TABLE editions (id INTEGER PRIMARY KEY, number INTEGER NOT NULL UNIQUE, date TEXT NOT NULL,
          status TEXT NOT NULL, pdf_path TEXT, puzzle_type TEXT, puzzle_solution TEXT,
          log TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL);
        INSERT INTO editions (number, date, status, created_at, puzzle_solution)
          VALUES (1, '2026-10-12', 'printed', 'x', '{"kind": "sudoku"}');""")
    raw.commit()
    raw.close()
    conn = db.connect(old)
    assert db.get_edition(conn, 1).is_preview is False  # existing rows default to real editions
    db.connect(old).close()  # migrating twice is harmless
    p = db.create_edition(conn, "2026-10-13", preview=True)
    n3 = db.create_edition(conn, "2026-10-14")
    assert p.is_preview and not n3.is_preview
    db.update_edition(conn, p.id, puzzle_solution='{"kind": "sudoku", "p": 1}')
    db.update_edition(conn, n3.id, puzzle_solution='{"kind": "sudoku", "p": 2}')
    assert db.previous_answers_edition(conn, 3).number == 1  # the preview in between is ignored
    assert db.previous_answers_edition(conn, 4).number == 3
    assert db.next_daily_number(conn, exclude_id=p.id) == 3  # editions 1 and 3 count; a preview does not
    assert db.next_daily_number(conn, exclude_id=n3.id) == 2
    db.update_edition(conn, p.id, is_preview=False)
    assert db.next_daily_number(conn, exclude_id=n3.id) == 3


def test_old_single_ical_url_migrates_to_a_calendar(conn):
    old = {"paper_name": "The Rick Times", "ical_url": "https://cal.example.com/private/basic.ics"}
    conn.execute("INSERT INTO settings (id, data) VALUES (1, ?)", (json.dumps(old),))
    s = db.load_settings(conn)
    assert s.paper_name == "The Rick Times"
    assert [(c.name, c.url) for c in s.calendars] == [("Calendar", "https://cal.example.com/private/basic.ics")]
    assert "ical_url" not in s.model_dump()
    db.save_settings(conn, s)
    assert "ical_url" not in conn.execute("SELECT data FROM settings").fetchone()["data"]
    assert db.load_settings(conn).calendars == s.calendars


def test_old_blank_ical_url_migrates_to_no_calendars(conn):
    conn.execute("INSERT INTO settings (id, data) VALUES (1, ?)", (json.dumps({"ical_url": ""}),))
    assert db.load_settings(conn).calendars == []
