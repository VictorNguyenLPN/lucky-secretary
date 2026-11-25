"""Application configuration loaded and validated from environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from lucky_secretary.utils.datetime import validate_timezone


class Settings(BaseSettings):
    """Runtime settings for Lucky Secretary."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram
    telegram_bot_token: str = Field(..., min_length=1)
    telegram_allowed_user_ids: str = Field(..., min_length=1)

    # LLM (OpenAI-compatible; defaults to Gemini Free Tier endpoint)
    openai_api_key: str = Field(..., min_length=1)
    openai_model: str = "gemini-3.5-flash-lite"
    openai_base_url: str | None = "https://generativelanguage.googleapis.com/v1beta/openai/"

    # Google Calendar OAuth 2.0
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8080/"
    google_token_path: Path = Path("./data/google_token.json")
    google_token_json: str = ""

    # Application
    app_env: str = "development"
    port: int | None = None
    default_timezone: str = "Asia/Ho_Chi_Minh"
    default_event_duration: int = Field(default=60, gt=0)
    confirmation_timeout_minutes: int = Field(default=15, gt=0)

    # Database
    database_url: str = "sqlite+aiosqlite:///./data/lucky_secretary.db"

    @field_validator("telegram_bot_token", "openai_api_key", mode="before")
    @classmethod
    def _strip_required_secret(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Required secret environment variable must not be empty.")
        return value.strip()

    @field_validator("openai_base_url", mode="before")
    @classmethod
    def _normalize_base_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped if stripped else None

    @field_validator("port", mode="before")
    @classmethod
    def _normalize_port(cls, value: str | int | None) -> int | None:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        return int(value)

    @field_validator("default_timezone")
    @classmethod
    def _validate_default_timezone(cls, value: str) -> str:
        validate_timezone(value)
        return value.strip()

    @field_validator("telegram_allowed_user_ids")
    @classmethod
    def _validate_allowed_user_ids(cls, value: str) -> str:
        parsed = _parse_user_ids(value)
        if not parsed:
            raise ValueError("TELEGRAM_ALLOWED_USER_IDS must contain at least one integer ID.")
        return value

    @property
    def allowed_user_ids(self) -> set[int]:
        """Return the set of authorized Telegram numeric user IDs."""
        return _parse_user_ids(self.telegram_allowed_user_ids)

    @property
    def sqlite_path(self) -> str:
        """Extract the SQLite filesystem path from DATABASE_URL."""
        for prefix in ("sqlite+aiosqlite:///", "sqlite:///"):
            if self.database_url.startswith(prefix):
                return self.database_url[len(prefix) :]
        return self.database_url


def _parse_user_ids(raw: str) -> set[int]:
    ids: set[int] = set()
    for part in raw.split(","):
        item = part.strip()
        if item:
            ids.add(int(item))
    return ids


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached validated Settings instance."""
    return Settings()

