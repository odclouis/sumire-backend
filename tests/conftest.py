"""Shared pytest fixtures for the test suite."""

import pytest


@pytest.fixture(autouse=True)
def fake_supabase_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set fake Supabase credentials so Settings validation succeeds during tests."""
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test-service-key")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test-anon-key")
