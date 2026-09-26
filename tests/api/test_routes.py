"""Smoke tests: wiring and status codes only. Behavior is tested at the service seam."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.adapters.clock import FixedClock
from app.adapters.config.yaml_loader import load_client_config
from app.adapters.llm.fake_llm import FakeLLM
from app.adapters.persistence.sqlite_repo import SqliteCandidateRepository, create_sqlite_engine
from app.api.deps import get_recruiter_service, get_screening_service
from app.api.main import app
from app.application.recruiter_service import RecruiterService
from app.application.screening_service import ScreeningService

HANDLE = "34600111222"


@pytest.fixture
def client():
    config = load_client_config("grupo_sazon")
    repo = SqliteCandidateRepository(create_sqlite_engine("sqlite://"))
    screening = ScreeningService(
        config=config,
        llm=FakeLLM(),
        repo=repo,
        clock=FixedClock(datetime(2026, 9, 26, tzinfo=UTC)),
    )
    recruiter = RecruiterService(config=config, repo=repo)
    app.dependency_overrides[get_screening_service] = lambda: screening
    app.dependency_overrides[get_recruiter_service] = lambda: recruiter
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


def candidate_id(client) -> int:
    client.post("/api/applications", json={"phone": HANDLE})
    [row] = client.get("/api/candidates").json()
    return row["id"]


def test_api_list_candidates(client):
    client.post("/api/applications", json={"phone": HANDLE})
    response = client.get("/api/candidates", params={"status": "in_progress"})
    assert response.status_code == 200
    assert len(response.json()) == 1


def test_api_get_candidate(client):
    response = client.get(f"/api/candidates/{candidate_id(client)}")
    assert response.status_code == 200
    assert response.json()["handle"] == HANDLE


def test_api_get_unknown_candidate(client):
    assert client.get("/api/candidates/999").status_code == 404


def test_page_dashboard(client):
    client.post("/api/applications", json={"phone": HANDLE})
    response = client.get("/dashboard", params={"status": "in_progress"})
    assert response.status_code == 200
    assert 'href="/chat"' in response.text
    assert HANDLE in response.text


def test_page_candidate_detail(client):
    response = client.get(f"/dashboard/candidates/{candidate_id(client)}")
    assert response.status_code == 200
    assert HANDLE in response.text


def test_page_unknown_candidate(client):
    assert client.get("/dashboard/candidates/999").status_code == 404
