import os
import pytest
from flake_analysis.api.settings import Settings


def test_settings_defaults():
    """Settings have sensible defaults when no env vars are set."""
    # Clear any existing env vars
    for key in ["SAA_BIND_HOST", "SAA_BIND_PORT", "SAA_ALLOWED_ORIGINS"]:
        os.environ.pop(key, None)

    s = Settings()
    assert s.bind_host == "127.0.0.1"
    assert s.bind_port == 8000
    assert s.log_level == "info"
    assert s.log_format == "json"
    assert s.allowed_origins == []


def test_settings_from_env():
    """Settings read from env vars."""
    os.environ["SAA_BIND_HOST"] = "0.0.0.0"
    os.environ["SAA_BIND_PORT"] = "9000"
    os.environ["SAA_ALLOWED_ORIGINS"] = "http://localhost:5173,https://saa.example.com"

    s = Settings()
    assert s.bind_host == "0.0.0.0"
    assert s.bind_port == 9000
    assert s.allowed_origins == ["http://localhost:5173", "https://saa.example.com"]

    # Cleanup
    for key in ["SAA_BIND_HOST", "SAA_BIND_PORT", "SAA_ALLOWED_ORIGINS"]:
        os.environ.pop(key, None)
