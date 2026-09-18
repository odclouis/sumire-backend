"""Shared pytest fixtures for the test suite."""

import pytest


@pytest.fixture(autouse=True)
def fake_supabase_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set fake required env vars so Settings validation succeeds during tests."""
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test-service-key")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test-anon-key")
    monkeypatch.setenv("WHATSAPP_ACCESS_TOKEN", "test-whatsapp-token")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "test-phone-number-id")
    monkeypatch.setenv("WHATSAPP_BUSINESS_ACCOUNT_ID", "test-business-account-id")
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "test-app-secret")
    monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "test-verify-token")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
