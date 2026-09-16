"""Tests for the /health endpoint."""

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app

client = TestClient(app)


def test_health_reports_connected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify /health reports a connected database when the connection check succeeds."""
    monkeypatch.setattr(db, "check_database_connection", lambda: True)
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "connected"
    assert body["environment"] == "development"


def test_health_reports_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify /health reports an error status when the connection check fails."""
    monkeypatch.setattr(db, "check_database_connection", lambda: False)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["database"] == "error"
