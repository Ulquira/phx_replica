from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator


def _parse_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


class Settings(BaseModel):
    """Typed application settings loaded from environment variables."""

    # Azure SQL (source)
    azure_server: Optional[str] = None
    azure_database: Optional[str] = None
    azure_user: Optional[str] = None
    azure_password: Optional[str] = None
    azure_trust_server_cert: bool = False
    azure_conn_timeout: int = Field(default=30, ge=5, le=120)

    # MySQL (target)
    mysql_host: Optional[str] = None
    mysql_port: int = Field(default=3306, ge=1, le=65535)
    mysql_database: Optional[str] = None
    mysql_user: Optional[str] = None
    mysql_password: Optional[str] = None
    mysql_table: str = "vw_winordetraba"
    mysql_ssl_disabled: bool = False
    mysql_conn_timeout: int = Field(default=15, ge=5, le=120)

    # Sync parameters
    sync_lookback_days: int = Field(default=1, ge=0, le=365)

    # Gemini photo enhancement
    gemini_api_key: Optional[str] = None
    gemini_image_model: str = "gemini-2.5-flash-image"
    photo_batch_size: int = Field(default=50, ge=1, le=500)
    photo_max_batches: int = Field(default=200, ge=1, le=5000)

    @field_validator("mysql_table")
    @classmethod
    def validate_mysql_table(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("MYSQL_TABLE cannot be empty")
        return cleaned

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        return cls(
            azure_server=os.getenv("AZURE_SERVER") or None,
            azure_database=os.getenv("AZURE_DATABASE") or None,
            azure_user=os.getenv("AZURE_USER") or None,
            azure_password=os.getenv("AZURE_PASSWORD") or None,
            azure_trust_server_cert=_parse_bool(os.getenv("AZURE_TRUST_SERVER_CERT"), default=False),
            azure_conn_timeout=int(os.getenv("AZURE_CONN_TIMEOUT", "30")),
            mysql_host=os.getenv("MYSQL_HOST") or None,
            mysql_port=int(os.getenv("MYSQL_PORT", "3306")),
            mysql_database=os.getenv("MYSQL_DATABASE") or None,
            mysql_user=os.getenv("MYSQL_USER") or None,
            mysql_password=os.getenv("MYSQL_PASSWORD") or None,
            mysql_table=os.getenv("MYSQL_TABLE", "vw_winordetraba"),
            mysql_ssl_disabled=_parse_bool(os.getenv("MYSQL_SSL_DISABLED"), default=False),
            mysql_conn_timeout=int(os.getenv("MYSQL_CONN_TIMEOUT", "15")),
            sync_lookback_days=int(os.getenv("SYNC_LOOKBACK_DAYS", "1")),
            gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
            gemini_image_model=os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image"),
            photo_batch_size=int(os.getenv("PHOTO_BATCH_SIZE", "50")),
            photo_max_batches=int(os.getenv("PHOTO_MAX_BATCHES", "200")),
        )

    def require_azure(self) -> None:
        missing = [
            name
            for name, val in (
                ("AZURE_SERVER", self.azure_server),
                ("AZURE_DATABASE", self.azure_database),
                ("AZURE_USER", self.azure_user),
                ("AZURE_PASSWORD", self.azure_password),
            )
            if not val
        ]
        if missing:
            raise RuntimeError(f"Missing required Azure SQL environment variables: {', '.join(missing)}")

    def require_mysql(self) -> None:
        missing = [
            name
            for name, val in (
                ("MYSQL_HOST", self.mysql_host),
                ("MYSQL_DATABASE", self.mysql_database),
                ("MYSQL_USER", self.mysql_user),
                ("MYSQL_PASSWORD", self.mysql_password),
            )
            if not val
        ]
        if missing:
            raise RuntimeError(f"Missing required MySQL environment variables: {', '.join(missing)}")

    def require_gemini(self) -> str:
        if not self.gemini_api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not configured. Set it in the environment or .env with a valid Google AI Studio key."
            )
        return self.gemini_api_key


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
