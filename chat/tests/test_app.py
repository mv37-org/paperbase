from __future__ import annotations

from fastapi.testclient import TestClient

from paperbase_chat.app import app


def test_home_renders_static_shell():
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "paperbase" in response.text
    assert "Paperbase helps AI researchers and agent builders keep up with fast-moving work without losing track of the original sources." in response.text
    assert "Ask broad questions, compare ideas across papers, and pull out the design patterns that matter" in response.text
    assert 'href="https://mv37.org"' in response.text
    assert "Continue with Google" in response.text
    assert "Create a new account" in response.text
    assert "Already have an account? Log in" in response.text
    assert "Forgot password" in response.text
    assert "Use your account to continue." in response.text
    assert 'id="auth-tabs"' not in response.text
    assert "/static/editor.js" in response.text
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
