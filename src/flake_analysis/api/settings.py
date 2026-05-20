"""Application settings from environment variables."""
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Env-driven config per deployment design §8.3."""

    bind_host: str = "127.0.0.1"
    bind_port: int = 8000
    log_level: str = "info"
    log_format: str = "json"
    allowed_origins: Annotated[list[str], NoDecode] = []
    analysis_roots: Annotated[list[str], NoDecode] = ["/mnt/analysis"]
    raw_roots: Annotated[list[str], NoDecode] = ["/mnt/raw_images"]
    cache_dir: str | None = None

    model_config = SettingsConfigDict(
        env_prefix="SAA_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_csv(cls, v):
        """Parse comma-separated origins."""
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        return v or []

    @field_validator("analysis_roots", "raw_roots", mode="before")
    @classmethod
    def parse_roots_csv(cls, v):
        """Parse comma-separated paths."""
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        return v or []
