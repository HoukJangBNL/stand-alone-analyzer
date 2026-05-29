"""Measurement & model-swap utilities — prod-grade unit methods.

These functions are the boundary between systemd-managed worker state
and ad-hoc Python (measurement scripts, future prod GPU dispatcher).

Currently shipped:
* :func:`load_worker_env` — bridge systemd EnvironmentFile= → os.environ

To be added in subsequent plan tasks:
* ``resolve_model_meta``  — local path or s3:// URI → deterministic
                             local artifact + name/sha256/source_uri
                             metadata
* ``build_defer_payload`` — kwargs for app.configure_task('run_sam').defer

Designed to be called from:
* ``scripts/sam/measure-defer.py`` (this plan)
* future prod GPU dispatcher (out of scope here)
"""
from __future__ import annotations

from pathlib import Path


def load_worker_env(env_file: Path = Path("/etc/flake-analysis-worker.env")) -> dict[str, str]:
    """Parse a systemd-style EnvironmentFile into a dict of env vars.

    Supports::

        KEY=value
        KEY="quoted value with spaces"
        KEY='single quoted'
        # comment lines (any leading whitespace)
        <blank lines>

    Raises:
        FileNotFoundError: env_file does not exist.
        ValueError: any non-blank, non-comment line is missing '='.
    """
    env_file = Path(env_file)
    if not env_file.exists():
        raise FileNotFoundError(f"worker env file not found: {env_file}")

    out: dict[str, str] = {}
    for lineno, raw in enumerate(env_file.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(
                f"malformed line {lineno} in {env_file}: missing '='"
            )
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # Strip matching surrounding quotes — single or double.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        out[key] = value
    return out
