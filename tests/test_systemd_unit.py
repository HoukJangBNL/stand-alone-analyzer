# tests/test_systemd_unit.py
"""systemd unit-file shape test (Plan 5 Task 5).

Asserts the unit defines the keys deployment-design.md §5.1 requires:
- ExecStart points at the FastAPI app module path verified in Plan 1
  (`flake_analysis.api.main:app`).
- Restart=on-failure (so a crash recovers, but a clean exit doesn't loop).
- User=<EDIT-ME> placeholder so the deploy operator must fill it in.
- Environment lines for HOME, SAA_BIND_HOST, SAA_BIND_PORT.
- KillMode=mixed (deployment-design §5.1 — covers the Streamlit-cache leak fix).
"""
from __future__ import annotations
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
UNIT_PATH = REPO_ROOT / "deploy" / "systemd" / "saa-api.service"


def test_unit_file_exists():
    assert UNIT_PATH.exists(), f"missing systemd unit at {UNIT_PATH}"


def test_unit_has_user_placeholder():
    text = UNIT_PATH.read_text(encoding="utf-8")
    assert "User=<EDIT-ME>" in text, "unit must use <EDIT-ME> placeholder for User="
    assert "# User=" in text or "<EDIT-ME>" in text, (
        "unit must signal the operator must fill User in"
    )


def test_unit_has_required_keys():
    text = UNIT_PATH.read_text(encoding="utf-8")
    assert "[Unit]" in text
    assert "[Service]" in text
    assert "[Install]" in text
    assert "Restart=on-failure" in text
    assert "Type=exec" in text or "Type=simple" in text


def test_unit_execstart_targets_fastapi_app():
    text = UNIT_PATH.read_text(encoding="utf-8")
    # Pinned decision: ExecStart=/opt/saa/.venv/bin/uvicorn flake_analysis.api.main:app ...
    assert "/opt/saa/.venv/bin/uvicorn" in text
    assert "flake_analysis.api.main:app" in text
    assert "--host 127.0.0.1" in text
    assert "--port 8000" in text


def test_unit_environment_lines_present():
    text = UNIT_PATH.read_text(encoding="utf-8")
    assert "Environment=HOME=" in text
    assert "Environment=SAA_BIND_HOST=" in text
    assert "Environment=SAA_BIND_PORT=" in text
    assert "Environment=PYTHONUNBUFFERED=1" in text


def test_unit_kill_semantics():
    text = UNIT_PATH.read_text(encoding="utf-8")
    # deployment-design §5.1: KillMode=mixed reaps the Streamlit-cache leak case
    assert "KillMode=mixed" in text
    assert "TimeoutStopSec=" in text


def test_unit_targets_multi_user():
    text = UNIT_PATH.read_text(encoding="utf-8")
    assert "WantedBy=multi-user.target" in text
