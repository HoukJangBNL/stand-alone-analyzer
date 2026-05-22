"""Unit tests for dev-bypass + prod-leak guard (W6.2.4).

The prod guard ensures SAA_AUTH_DEV_BYPASS=1 cannot leak into production.
When both SAA_AUTH_DEV_BYPASS=1 and SAA_ENV=prod, module startup raises
RuntimeError, preventing any requests from being served.
"""
from __future__ import annotations

import importlib
import os
import sys

import pytest


def test_bypass_blocked_in_prod(monkeypatch):
    """Dev bypass hard-fails when SAA_ENV=prod."""
    monkeypatch.setenv("SAA_AUTH_DEV_BYPASS", "1")
    monkeypatch.setenv("SAA_ENV", "prod")

    # Remove module from cache to force fresh import
    if "flake_analysis.api.auth.dev_bypass" in sys.modules:
        del sys.modules["flake_analysis.api.auth.dev_bypass"]

    with pytest.raises(RuntimeError, match="dev-bypass.*prod"):
        import flake_analysis.api.auth.dev_bypass  # noqa: F401


def test_bypass_mints_local_admin(monkeypatch):
    """Dev bypass mints admin user with dev:local cognito_sub."""
    monkeypatch.setenv("SAA_AUTH_DEV_BYPASS", "1")
    monkeypatch.setenv("SAA_ENV", "dev")

    # Remove module from cache to force fresh import
    if "flake_analysis.api.auth.dev_bypass" in sys.modules:
        del sys.modules["flake_analysis.api.auth.dev_bypass"]

    import flake_analysis.api.auth.dev_bypass as m

    importlib.reload(m)
    u = m.mint_dev_user()
    assert u.role.value == "admin"
    assert u.cognito_sub == "dev:local"
    assert u.email == "local@dev"


def test_bypass_disabled_by_default(monkeypatch):
    """Without SAA_AUTH_DEV_BYPASS=1, the module loads normally."""
    monkeypatch.delenv("SAA_AUTH_DEV_BYPASS", raising=False)
    monkeypatch.setenv("SAA_ENV", "prod")

    # Remove module from cache to force fresh import
    if "flake_analysis.api.auth.dev_bypass" in sys.modules:
        del sys.modules["flake_analysis.api.auth.dev_bypass"]

    # Should not raise
    import flake_analysis.api.auth.dev_bypass  # noqa: F401
