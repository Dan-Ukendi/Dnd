from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

WA_DEFAULT_BASE_URL = "https://www.worldanvil.com/api/external/boromir"


def normalize_database_url(url: str) -> str:
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str
    database_url_unpooled: str | None = None
    secret_key: SecretStr = Field(min_length=32)
    wa_application_key: SecretStr = SecretStr("")
    wa_auth_token: SecretStr = SecretStr("")
    wa_world_id: str = ""
    app_env: Literal["production", "development", "test"] = "production"
    wa_base_url: str = WA_DEFAULT_BASE_URL
    wa_user_agent: str = "Campaign Companion (self-hosted, 1.0.0)"
    session_max_age_seconds: int = 60 * 60 * 24 * 14
    invitation_ttl_hours: int = 72
    player_initial_password: SecretStr = SecretStr("0000")
    player_password_change: bool = False

    @field_validator("database_url", "database_url_unpooled")
    @classmethod
    def _normalize_url(cls, value: str | None) -> str | None:
        return normalize_database_url(value) if value else value

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def migration_database_url(self) -> str:
        return self.database_url_unpooled or self.database_url

    @property
    def world_anvil_configured(self) -> bool:
        return bool(
            self.wa_application_key.get_secret_value()
            and self.wa_auth_token.get_secret_value()
            and self.wa_world_id
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
