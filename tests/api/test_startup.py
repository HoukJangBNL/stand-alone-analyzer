"""B2 — fail fast when SAA_S3_BUCKET is unconfigured.

The presign / complete routes currently surface SAA_S3_BUCKET misconfig as
500 at request time. We want the failure to happen at app construction so
misconfigured deployments crash on boot instead of after a user uploads.
"""
from __future__ import annotations

import pytest

# Import once up top so the module-level `app = create_app()` in main.py
# runs against the real env (which has SAA_S3_BUCKET set via .env).
# Subsequent calls to create_app() inside tests can mutate env safely.
from flake_analysis.api.main import create_app


def test_app_construction_fails_when_s3_bucket_unset(monkeypatch):
    """create_app() must raise RuntimeError before serving any request."""
    # Empty string overrides whatever .env contributes (env vars take
    # precedence over env_file in pydantic-settings) and is treated as
    # "not configured" by the startup check.
    monkeypatch.setenv("SAA_S3_BUCKET", "")

    with pytest.raises(RuntimeError, match=r"(?i)SAA_S3_BUCKET.*(unset|required|not configured)"):
        create_app()


def test_app_construction_succeeds_when_s3_bucket_set(monkeypatch):
    """Sanity: with bucket configured, create_app() returns a FastAPI app."""
    monkeypatch.setenv("SAA_S3_BUCKET", "qpress-uploads-test")

    from fastapi import FastAPI

    app = create_app()
    assert isinstance(app, FastAPI)
