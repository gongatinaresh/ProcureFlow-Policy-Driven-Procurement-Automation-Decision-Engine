from functools import lru_cache
from pathlib import Path
from typing import Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Cantill Procurement API"
    environment: str = "development"
    log_level: str = "INFO"
    database_url: str = "sqlite:///./data/cantill.db"
    business_timezone: str = "Asia/Kolkata"
    policy_storage_dir: Path = Path("data/policies")
    auto_create_tables: bool = True
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    )
    api_prefix: str = "/api"

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ValueError("LOG_LEVEL must be a standard Python logging level")
        return normalized

    @field_validator("api_prefix")
    @classmethod
    def normalize_api_prefix(cls, value: str) -> str:
        normalized = "/" + value.strip("/")
        if normalized == "/":
            raise ValueError("API_PREFIX cannot be the root path")
        return normalized

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, value: list[str]) -> list[str]:
        normalized = [origin.strip().rstrip("/") for origin in value]
        if any(not origin for origin in normalized):
            raise ValueError("CORS_ORIGINS cannot contain empty origins")
        return normalized

    @field_validator("business_timezone")
    @classmethod
    def validate_business_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("BUSINESS_TIMEZONE must be a valid IANA timezone") from error
        return value

    @model_validator(mode="after")
    def disable_automatic_schema_creation_in_production(self) -> Self:
        if self.environment.lower() in {"production", "prod"} and self.auto_create_tables:
            raise ValueError(
                "AUTO_CREATE_TABLES must be false in production; apply Alembic migrations"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
