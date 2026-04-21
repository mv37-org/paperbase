from __future__ import annotations

from fastapi.testclient import TestClient

from paperbase_chat.app import app


def test_home_renders_static_shell():
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "paperbase" in response.text
    assert "Chat with your papers" in response.text
    assert "Grounded answers across your library and the literature." in response.text
    assert "Continue with Google" in response.text
    assert "__PAPERBASE_BASE_URL__" not in response.text


def test_static_assets_are_served():
    client = TestClient(app)

    response = client.get("/static/app.js")

    assert response.status_code == 200
    assert "PAPERBASE_BASE_URL" in response.text


def test_health_endpoint():
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
