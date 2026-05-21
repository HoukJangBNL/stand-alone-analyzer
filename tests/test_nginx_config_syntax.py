# tests/test_nginx_config_syntax.py
"""nginx config-syntax smoke test (Plan 5 Task 4).

Skips when the host has no `nginx` binary (CI / dev laptop case).
When `nginx` is available, runs `nginx -t -c <abs-conf-path>` and
asserts exit 0. The test does NOT load the served files; it only
proves the config parses.
"""
from __future__ import annotations
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
NGINX_CONF = REPO_ROOT / "deploy" / "nginx" / "stand-alone-analyzer.conf"


def test_nginx_conf_file_exists():
    assert NGINX_CONF.exists(), f"missing nginx config at {NGINX_CONF}"
    assert NGINX_CONF.stat().st_size > 0, "nginx config is empty"


def test_nginx_conf_contains_required_locations():
    text = NGINX_CONF.read_text(encoding="utf-8")
    assert "location /assets/" in text
    assert "location = /index.html" in text
    assert "location / {" in text
    assert "location /api/" in text
    assert "location /_tiles_internal/" in text
    assert "location = /healthz" in text


def test_nginx_conf_pins_sse_proxy_settings():
    text = NGINX_CONF.read_text(encoding="utf-8")
    assert "proxy_buffering off" in text
    assert "proxy_read_timeout 1h" in text
    assert "proxy_send_timeout 1h" in text


def test_nginx_conf_marks_internal_tile_path():
    text = NGINX_CONF.read_text(encoding="utf-8")
    # `internal;` directive must appear inside /_tiles_internal/ block
    block_start = text.index("location /_tiles_internal/")
    block_end = text.index("}", block_start)
    block = text[block_start:block_end]
    assert "internal;" in block


def test_nginx_t_passes_when_nginx_available():
    if shutil.which("nginx") is None:
        pytest.skip("nginx binary not available on this host")
    # Run nginx -t against the bare server-block file. nginx requires a
    # full config (events {} + http {} wrapper), so we synthesize one
    # in a tmp file that includes our server block.
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as wrapper:
        wrapper.write(
            "events { worker_connections 1024; }\n"
            "http {\n"
            f"  include {NGINX_CONF};\n"
            "}\n"
        )
        wrapper_path = wrapper.name
    try:
        result = subprocess.run(
            ["nginx", "-t", "-c", wrapper_path],
            capture_output=True,
            text=True,
        )
        # Some nginx builds print test output to stderr regardless of success.
        assert result.returncode == 0, (
            f"nginx -t failed:\nstdout={result.stdout}\nstderr={result.stderr}"
        )
    finally:
        Path(wrapper_path).unlink(missing_ok=True)
