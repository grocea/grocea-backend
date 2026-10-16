from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "local"
    database_url: str = "postgresql+psycopg://localhost:5432/grocea"
    test_database_url: str = "postgresql+psycopg://localhost:5432/grocea_test"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    trusted_hosts: str = "localhost,127.0.0.1,::1,testserver"
    auth_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    public_app_origin: str = ""
    smtp_host: str = ""
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_security: Literal["starttls", "ssl", "none"] = "starttls"
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from: str = ""
    smtp_timeout_seconds: float = Field(default=5, gt=0, le=15)
    recovery_throttle_secret: SecretStr = SecretStr("")
    reset_token_minutes: int = Field(default=30, ge=1, le=60)
    reset_requests_per_hour: int = Field(default=5, ge=1, le=20)
    log_level: str = "INFO"
    api_log_capacity: int = Field(default=500, ge=1, le=10_000)

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def trusted_host_list(self) -> list[str]:
        return [host.strip() for host in self.trusted_hosts.split(",") if host.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
