"""Unit tests for the prod-grade measurement utility module."""
from __future__ import annotations

from pathlib import Path

import pytest


def test_load_worker_env_basic(tmp_path: Path) -> None:
    from flake_analysis.worker.measurement import load_worker_env

    env_file = tmp_path / "worker.env"
    env_file.write_text(
        "SAA_DB_HOST=qpressdb.example.com\n"
        "SAA_DB_PORT=5432\n"
        "SAA_DB_NAME=qpress\n"
    )
    out = load_worker_env(env_file)
    assert out == {
        "SAA_DB_HOST": "qpressdb.example.com",
        "SAA_DB_PORT": "5432",
        "SAA_DB_NAME": "qpress",
    }


def test_load_worker_env_quoted_values(tmp_path: Path) -> None:
    from flake_analysis.worker.measurement import load_worker_env

    env_file = tmp_path / "worker.env"
    env_file.write_text(
        'SAA_DB_PASSWORD="hunter2 with spaces"\n'
        "SAA_DB_USER='uname'\n"
    )
    out = load_worker_env(env_file)
    assert out["SAA_DB_PASSWORD"] == "hunter2 with spaces"
    assert out["SAA_DB_USER"] == "uname"


def test_load_worker_env_skips_blank_and_comment_lines(tmp_path: Path) -> None:
    from flake_analysis.worker.measurement import load_worker_env

    env_file = tmp_path / "worker.env"
    env_file.write_text(
        "# top comment\n"
        "\n"
        "SAA_DB_HOST=h\n"
        "  # indented comment\n"
        "SAA_DB_PORT=5432\n"
    )
    out = load_worker_env(env_file)
    assert out == {"SAA_DB_HOST": "h", "SAA_DB_PORT": "5432"}


def test_load_worker_env_missing_file_raises(tmp_path: Path) -> None:
    from flake_analysis.worker.measurement import load_worker_env

    with pytest.raises(FileNotFoundError):
        load_worker_env(tmp_path / "nonexistent.env")


def test_load_worker_env_malformed_line_raises(tmp_path: Path) -> None:
    from flake_analysis.worker.measurement import load_worker_env

    env_file = tmp_path / "worker.env"
    env_file.write_text("LINE_WITHOUT_EQUALS\nSAA_DB_HOST=h\n")
    with pytest.raises(ValueError, match="malformed"):
        load_worker_env(env_file)
