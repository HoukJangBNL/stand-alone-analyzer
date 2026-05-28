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


_LOCAL_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})


def _require_ssl(host: str) -> bool:
    """True iff ``host`` is a remote host that should force SSL.

    Local Postgres instances used in tests/dev typically do not have SSL
    configured, so an unconditional ``require`` would break them. RDS
    (and any non-localhost target) must be SSL-only — see #217.
    """
    return host not in _LOCAL_HOSTS


def get_db_url(async_driver: bool = True) -> str:
    """Build a Postgres URL from SAA_DB_* env vars.

    For non-local hosts, force-enables SSL via a driver-appropriate
    query parameter:

    - asyncpg uses ``ssl=require`` (its own kwarg vocabulary; ``sslmode``
      is not recognised by ``asyncpg.connect``).
    - psycopg uses libpq's ``sslmode=require``.

    RDS is configured with ``rds.force_ssl=1``; setting ``require`` here
    suppresses libpq's ``prefer→fallback`` retry path so SSL/auth
    failures surface cleanly instead of hitting the no-encryption
    rejection (Refs: #211, #217).
    """
    s = DbSettings()
    driver = "postgresql+asyncpg" if async_driver else "postgresql+psycopg"
    user = quote_plus(s.db_user)
    password = quote_plus(s.db_password)
    auth = f"{user}:{password}@" if s.db_user else ""
    base = f"{driver}://{auth}{s.db_host}:{s.db_port}/{s.db_name}"
    if not _require_ssl(s.db_host):
        return base
    ssl_param = "ssl=require" if async_driver else "sslmode=require"
    return f"{base}?{ssl_param}"
