import json
import re
from pathlib import Path

from morning_edition import db


def test_today_empty_state(authed):
    r = authed.get("/")
    assert r.status_code == 200 and "No editions yet" in r.text


def test_build_now_creates_edition_without_printing(authed, app):
    r = authed.post("/build", follow_redirects=False)
    assert r.status_code == 303
    app.state.last_thread.join(timeout=120)
    conn = db.connect(app.state.db_path)
    (ed,) = db.list_editions(conn)
    assert ed.status == "built" and Path(ed.pdf_path).exists()
    assert app.state.services.printer.printed == []
    page = authed.get("/").text
    assert "No. 1" in page and "/editions/1/pdf" in page


def test_pdf_route_and_reprint(authed, app):
    authed.post("/build")
    app.state.last_thread.join(timeout=120)
    r = authed.get("/editions/1/pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    authed.post("/editions/1/print")
    app.state.last_thread.join(timeout=30)
    assert len(app.state.services.printer.printed) == 1
    assert "Archive" in authed.get("/editions").text


def test_pdf_route_rejects_paths_outside_data_dir(authed, app):
    conn = db.connect(app.state.db_path)
    ed = db.create_edition(conn, "2026-10-12")
    db.update_edition(conn, ed.id, pdf_path="/etc/hosts")
    assert authed.get(f"/editions/{ed.id}/pdf").status_code == 404


def test_printer_test_page(authed, app):
    r = authed.post("/printer/test")
    assert r.status_code == 200 and "Test page sent" in r.text
    assert len(app.state.services.printer.printed) == 1


def test_edition_routes_require_login(client):
    assert client.post("/build", follow_redirects=False).status_code == 303
    assert client.get("/editions/1/pdf", follow_redirects=False).status_code == 303


def test_startup_removes_stale_test_page_files(app):
    import os
    import time

    from fastapi.testclient import TestClient

    data_dir = app.state.config.data_dir
    old, fresh = data_dir / "tmpold.pdf", data_dir / "tmpnew.pdf"
    old.write_bytes(b"%PDF")
    fresh.write_bytes(b"%PDF")
    two_days_ago = time.time() - 2 * 86400
    os.utime(old, (two_days_ago, two_days_ago))
    with TestClient(app):
        pass
    assert not old.exists() and fresh.exists()


def test_printer_test_deletes_temp_pdf(authed, app):
    authed.post("/printer/test")
    assert list(app.state.config.data_dir.glob("tmp*.pdf")) == []


def test_printer_test_render_failure_is_shown_not_500(authed, monkeypatch):
    from morning_edition.web import app as app_module

    def boom(settings, when):
        raise RuntimeError("no fonts")

    monkeypatch.setattr(app_module, "render_test_page", boom)
    r = authed.post("/printer/test")
    assert r.status_code == 200 and "Printer problem: no fonts" in r.text


def test_app_configures_morning_edition_logger(app):
    import logging
    logger = logging.getLogger("morning_edition")
    assert logger.level == logging.INFO
    handlers = [h for h in logger.handlers if getattr(h, "_morning_edition", False)]
    assert len(handlers) == 1  # created once, even when several apps are built


def test_build_now_makes_a_preview_and_the_pages_say_so(authed, app):
    authed.post("/build")
    app.state.last_thread.join(timeout=120)
    (ed,) = db.list_editions(db.connect(app.state.db_path))
    assert ed.is_preview
    assert 'badge preview">preview' in authed.get("/").text
    assert 'badge preview">preview' in authed.get("/editions").text


def test_scheduled_job_builds_a_real_edition_and_waits_for_a_running_build(app):
    import threading
    import time

    from morning_edition import pipeline

    pipeline.BUILD_LOCK.acquire()
    threading.Timer(0.3, pipeline.BUILD_LOCK.release).start()
    start = time.monotonic()
    app.state.scheduled_job()
    assert time.monotonic() - start >= 0.25
    (ed,) = db.list_editions(db.connect(app.state.db_path))
    assert not ed.is_preview and ed.status in ("built", "printed")


def test_scheduled_job_logs_an_error_when_it_gives_up(app, monkeypatch, caplog):
    from morning_edition import pipeline
    from morning_edition.web import app as app_module

    monkeypatch.setattr(app_module, "SCHEDULED_WAIT_SECONDS", 0.05)
    with pipeline.BUILD_LOCK, caplog.at_level("ERROR", logger="morning_edition"):
        app.state.scheduled_job()
    assert "Scheduled edition skipped" in caplog.text
    assert db.list_editions(db.connect(app.state.db_path)) == []


def test_today_says_the_paper_prints_when_ready(authed, app):
    from datetime import datetime
    from types import SimpleNamespace

    job = SimpleNamespace(next_run_time=datetime(2026, 10, 13, 5, 10))
    app.state.scheduler = SimpleNamespace(get_job=lambda job_id: job)
    text = authed.get("/").text
    assert "Next edition builds Tuesday at 5:10 AM" in text
    assert "prints when it is ready, around 05:30" in text
    assert "prints at" not in text


# ---- Today's paper: the list of articles with links to the originals ----

def make_edition(app, placed=None, preview=False, status="built"):
    """An edition row (and, when `placed` is given, its placed.json) without running a build."""
    conn = db.connect(app.state.db_path)
    ed = db.create_edition(conn, "2026-10-12", preview=preview)
    db.update_edition(conn, ed.id, status=status)
    if placed is not None:
        work = app.state.config.data_dir / "editions" / f"{ed.number:04d}"
        work.mkdir(parents=True)
        (work / "placed.json").write_text(json.dumps(placed))
    return ed


def pc(headline, section="Local", kind="story", url=None, source="The Gazette", **extra):
    url = f"https://example.com/{headline.replace(' ', '-').lower()}" if url is None else url
    return {"item_id": 1, "section": section, "kind": kind, "headline": headline, "deck": "",
            "paragraphs": ["p"], "source": source, "key": url, "url": url, **extra}


def paper_section(page):
    return page[page.index("Today's paper"):]


def test_today_lists_the_papers_pieces_grouped_by_section_with_links(authed, app):
    make_edition(app, {"pieces": [
        pc("Seawall vote", "Local", "lead"), pc("Tech item", "Tech"), pc("World item", "World"),
        pc("Second local", "Local", "brief"), pc("Cheerful news", "Local", "good_news", good_news=True)],
        "ranks": {}, "also": [{"title": "Headline only", "source": "Wire", "url": "https://wire.example.com/h"}]})
    page = authed.get("/").text
    block = paper_section(page)
    order = ["Local", "World", "Tech", "Good News", "Also in the news"]
    heads = re.findall(r"<h3>([^<]+)</h3>", block)
    assert heads == order
    assert block.index("Seawall vote") < block.index("Second local") < block.index("World item")
    assert block.index("Cheerful news") > block.index("Tech item")
    assert block.index("Headline only") > block.index("Cheerful news")
    assert 'href="https://example.com/seawall-vote" target="_blank" rel="noopener noreferrer"' in block
    assert block.count("Read the original") == 6 and "The Gazette" in block
    assert 'href="https://wire.example.com/h"' in block
    assert page.index("Today's paper") < page.index('class="log"')


def test_today_paper_follows_the_configured_section_order(authed, app):
    conn = db.connect(app.state.db_path)
    db.save_settings(conn, db.load_settings(conn).model_copy(update={"sections": ["Tech", "World", "Local"]}))
    make_edition(app, {"pieces": [pc("A", "Local", "lead"), pc("B", "World"), pc("C", "Tech")],
                       "ranks": {}, "also": []})
    heads = re.findall(r"<h3>([^<]+)</h3>", paper_section(authed.get("/").text))
    assert heads == ["Tech", "World", "Local"]


def test_only_web_urls_become_links(authed, app):
    make_edition(app, {"pieces": [
        pc("Sneaky", url="javascript:alert(1)"), pc("Blank", url=""), pc("Data", url="data:text/html,x"),
        pc("Fine", url="HTTP://example.com/fine")], "ranks": {}, "also": [
        {"title": "Bad also", "source": "Wire", "url": "javascript:alert(2)"}]})
    block = paper_section(authed.get("/").text)
    assert "javascript:" not in block and "data:text" not in block
    assert block.count("<a ") == 1 and 'href="HTTP://example.com/fine"' in block
    for title in ("Sneaky", "Blank", "Data", "Bad also"):
        assert title in block
    assert "The Gazette" in block


def test_paper_list_escapes_feed_text(authed, app):
    make_edition(app, {"pieces": [pc('<script>alert(1)</script>', source="<b>Src</b>", url='https://e.com/?a="x"')],
                       "ranks": {}, "also": []})
    block = paper_section(authed.get("/").text)
    assert "<script>alert(1)</script>" not in block and "&lt;script&gt;" in block
    assert "<b>Src</b>" not in block and '"x"' not in block


def test_previews_show_their_list_too(authed, app):
    make_edition(app, {"pieces": [pc("Preview story", "Local", "lead")], "ranks": {}, "also": []}, preview=True)
    page = authed.get("/").text
    assert 'badge preview">preview' in page and "Preview story" in paper_section(page)


def test_edition_without_placed_json_falls_back_to_the_stories_table(authed, app):
    ed = make_edition(app)
    conn = db.connect(app.state.db_path)
    conn.executemany(
        "INSERT INTO stories (edition_id, url, section, kind, rank, title, word_count) VALUES (?, ?, ?, ?, 0, ?, 5)",
        [(ed.id, "https://example.com/a", "World", "story", "World item"),
         (ed.id, "https://example.com/lead", "Local", "lead", "Old lead"),
         (ed.id, "https://example.com/good", "Local", "good_news", "Old good news")])
    conn.commit()
    block = paper_section(authed.get("/").text)
    assert re.findall(r"<h3>([^<]+)</h3>", block) == ["Local", "World", "Good News"]
    assert 'href="https://example.com/a"' in block and "World item" in block
    assert "Old lead" in block and "Old good news" in block


def test_edition_with_no_pieces_still_shows_the_papers_heading_and_a_hint(authed, app):
    make_edition(app)
    page = authed.get("/").text
    assert "Today's paper" in page and '<div class="paper">' not in page
    assert 'No stories in this edition yet. Add feeds in <a href="/settings#feeds">Settings</a>.' in page


def test_no_stories_hint_is_hidden_while_a_build_is_running(authed, app):
    from morning_edition import pipeline
    make_edition(app, status="building")
    with pipeline.BUILD_LOCK:
        page = authed.get("/").text
    assert "Building now" in page and "No stories in this edition yet" not in page


def test_no_editions_at_all_keeps_the_original_empty_state(authed):
    page = authed.get("/").text
    assert "No editions yet" in page
    assert "Today's paper" not in page and "No stories in this edition yet" not in page


def test_corrupt_placed_json_does_not_break_the_page(authed, app):
    ed = make_edition(app)
    work = app.state.config.data_dir / "editions" / f"{ed.number:04d}"
    work.mkdir(parents=True)
    (work / "placed.json").write_text("{not json")
    assert authed.get("/").status_code == 200


def test_old_placed_json_without_urls_links_to_the_normalized_key(authed, app):
    old = {k: v for k, v in pc("Old piece", url="https://example.com/old").items() if k != "url"}
    nokey = {k: v for k, v in pc("No key at all").items() if k not in ("url", "key")}
    bad = {k: v for k, v in pc("Bad key", url="javascript:alert(1)").items() if k != "url"}
    make_edition(app, {"pieces": [old, nokey, bad], "ranks": {}})
    block = paper_section(authed.get("/").text)
    assert 'href="https://example.com/old"' in block and block.count("<a ") == 1
    assert "No key at all" in block and "Bad key" in block and "javascript:" not in block


def test_a_piece_without_a_section_goes_under_other(authed, app):
    make_edition(app, {"pieces": [pc("A", "Local", "lead"), pc("Orphan", section=""),
                                  {k: v for k, v in pc("Nameless").items() if k != "section"}],
                       "ranks": {}, "also": []})
    block = paper_section(authed.get("/").text)
    heads = re.findall(r"<h3>([^<]*)</h3>", block)
    assert heads == ["Local", "Other"] and "<h3></h3>" not in block
    assert block.index("Orphan") > block.index("<h3>Other</h3>") and "Nameless" in block
