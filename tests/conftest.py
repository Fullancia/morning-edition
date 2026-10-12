import pytest

from morning_edition import db


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.sqlite")
    yield c
    c.close()


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch):
    """Retry backoff never makes a test wait; tests of the backoff itself inject their own sleep."""
    monkeypatch.setattr("morning_edition.sources.http._sleep", lambda seconds: None)


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    """Hostnames resolve to a public address, so tests never depend on real DNS; tests of the
    public-only guard override this."""
    monkeypatch.setattr("morning_edition.sources.http.resolve", lambda host: ["93.184.216.34"])
