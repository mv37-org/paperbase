from __future__ import annotations

from fastapi.testclient import TestClient

from paperbase_chat.app import app


def test_home_renders_static_shell():
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "Paperbase | MV37" in response.text
    assert "Paperbase" in response.text
    assert "Research patterns for building better AI agents and systems." in response.text
    assert "Paperbase helps AI researchers and agent builders keep up with fast-moving work without losing track of the original sources." in response.text
    assert "Ask broad questions, compare ideas across papers, and pull out the design patterns that matter" in response.text
    assert 'href="https://mv37.org"' in response.text
    assert 'href="/static/favicon.png"' in response.text
    assert 'id="auth-tabs"' not in response.text
    assert 'id="root"' in response.text
    assert 'class="landing-page app-boot-fallback"' in response.text
    assert "/static/app.js" in response.text
    assert "/static/app.css" in response.text
    assert "__PAPERBASE_BASE_URL__" not in response.text


def test_static_assets_are_served():
    client = TestClient(app)

    response = client.get("/static/app.js")

    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]

    stylesheet = client.get("/static/app.css")

    assert stylesheet.status_code == 200
    assert "css" in stylesheet.headers["content-type"]

    favicon = client.get("/static/favicon.png")

    assert favicon.status_code == 200


def test_health_endpoint():
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
