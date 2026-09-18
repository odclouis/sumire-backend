"""Application configuration loaded from environment variables."""

from functools import lru_cache

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed application settings sourced from environment variables and .env."""

    supabase_url: str
    supabase_service_key: str
    supabase_anon_key: str
    whatsapp_access_token: str
    whatsapp_phone_number_id: str
    whatsapp_business_account_id: str
    whatsapp_app_secret: str
    whatsapp_verify_token: str
    gemini_api_key: str
    anthropic_api_key: str
    environment: str = "development"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    """Load and cache the application settings, raising a clear error if required vars are missing."""
    try:
        return Settings()
    except ValidationError as exc:
        raise RuntimeError(
            f"Missing or invalid required environment variables:\n{exc}"
        ) from exc
