import random

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from morning_edition.config import AppConfig
from morning_edition.pipeline import Services
from morning_edition.web.app import create_app


class FakePrinter:
    def __init__(self):
        self.printed = []

    def ensure_queue(self, address):
        pass

    def print_pdf(self, path):
        self.printed.append(path)
        return "job-1"


@pytest.fixture(autouse=True)
def network():
    """Web tests never touch the internet; individual tests add routes to this router."""
    with respx.mock(assert_all_called=False) as router:
        router.get(host="api.wikimedia.org").mock(return_value=httpx.Response(200, json={"selected": []}))
        yield router


@pytest.fixture
def app(tmp_path):
    config = AppConfig(data_dir=tmp_path, dashboard_password="hunter2", session_secret="test-secret")
    services = Services(http=httpx.Client(), llm_factory=lambda s: None, printer=FakePrinter(),
                        rng=random.Random(1))
    return create_app(config, services, start_scheduler=False)


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture
def authed(client):
    client.post("/login", data={"password": "hunter2"})
    return client
