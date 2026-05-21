"""Database URL construction from env-driven settings (SAA_DB_* prefix)."""
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict


class DbSettings(BaseSettings):
    """Postgres connection settings (env prefix: SAA_)."""

    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = ""
    db_password: str = ""
    db_name: str = "qpress"

    model_config = SettingsConfigDict(
        env_prefix="SAA_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


def get_db_url(async_driver: bool = True) -> str:
    """Build a Postgres URL from SAA_DB_* env vars."""
    s = DbSettings()
    driver = "postgresql+asyncpg" if async_driver else "postgresql+psycopg"
    user = quote_plus(s.db_user)
    password = quote_plus(s.db_password)
    auth = f"{user}:{password}@" if s.db_user else ""
    return f"{driver}://{auth}{s.db_host}:{s.db_port}/{s.db_name}"
