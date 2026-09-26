"""Smoke tests: wiring and status codes only. Behavior is tested at the service seam."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.adapters.clock import FixedClock
from app.adapters.config.yaml_loader import load_client_config
from app.adapters.llm.fake_llm import FakeLLM
from app.adapters.persistence.sqlite_repo import SqliteCandidateRepository, create_sqlite_engine
from app.api.deps import get_screening_service
from app.api.main import app
from app.application.screening_service import ScreeningService

HANDLE = "34600111222"


@pytest.fixture
def client():
    service = ScreeningService(
        config=load_client_config("grupo_sazon"),
        llm=FakeLLM(),
        repo=SqliteCandidateRepository(create_sqlite_engine("sqlite://")),
        clock=FixedClock(datetime(2026, 9, 26, tzinfo=UTC)),
    )
    app.dependency_overrides[get_screening_service] = lambda: service
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_api_create_application(client):
    response = client.post("/api/applications", json={"phone": "+34 600 111 222"})
    assert response.status_code == 201
    assert response.json()["handle"] == HANDLE


def test_api_post_message(client):
    client.post("/api/applications", json={"phone": HANDLE})
    response = client.post(f"/api/screenings/{HANDLE}/messages", json={"text": "yes"})
    assert response.status_code == 200
    assert response.json()["reply"]


def test_api_get_transcript(client):
    client.post("/api/applications", json={"phone": HANDLE})
    response = client.get(f"/api/screenings/{HANDLE}/transcript")
    assert response.status_code == 200
    assert len(response.json()["messages"]) == 1


def test_page_chat_start_form(client):
    assert client.get("/chat").status_code == 200


def test_page_apply_redirects_to_the_chat(client):
    response = client.post("/chat", data={"phone": HANDLE}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/chat/{HANDLE}"


def test_page_chat(client):
    client.post("/chat", data={"phone": HANDLE})
    assert client.get(f"/chat/{HANDLE}").status_code == 200


def test_page_post_message_returns_bubbles(client):
    client.post("/chat", data={"phone": HANDLE})
    response = client.post(f"/chat/{HANDLE}/messages", data={"text": "yes"})
    assert response.status_code == 200
    assert "yes" in response.text
