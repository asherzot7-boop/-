"""Application settings loaded from environment variables / .env file."""
from __future__ import annotations

from functools import lru_cache
from typing import Annotated
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _parse_ids(value: object) -> list[int]:
    if value is None or value == "":
        return []
    if isinstance(value, int):
        return [value]
    if isinstance(value, (list, tuple)):
        return [int(v) for v in value]
    return [int(part) for part in str(value).replace(";", ",").split(",") if part.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Secrets: SecretStr keeps them out of repr() and logs.
    bot_token: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    api_token: SecretStr | None = None

    openai_model: str = "gpt-4o-mini"
    openai_timeout: float = 60.0

    database_url: str = "sqlite+aiosqlite:///./data/nashriyot.db"

    # Comma separated Telegram IDs. Synced into the users table on start.
    director_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)
    staff_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)
    viewer_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)

    timezone: str = "Asia/Tashkent"
    daily_report_hour: int = 19
    reminder_hours_before: int = 24
    scheduler_interval_seconds: int = 60

    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_enabled: bool = True

    log_level: str = "INFO"

    @field_validator("director_ids", "staff_ids", "viewer_ids", mode="before")
    @classmethod
    def _ids(cls, value: object) -> list[int]:
        return _parse_ids(value)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def demo_mode(self) -> bool:
        return self.bot_token is None or not self.bot_token.get_secret_value()

    @property
    def llm_enabled(self) -> bool:
        return self.openai_api_key is not None and bool(self.openai_api_key.get_secret_value())

    def secret_values(self) -> list[str]:
        out = []
        for s in (self.bot_token, self.openai_api_key, self.api_token):
            if s is not None and s.get_secret_value():
                out.append(s.get_secret_value())
        return out


@lru_cache
def get_settings() -> Settings:
    return Settings()
