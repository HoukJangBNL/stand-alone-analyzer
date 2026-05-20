"""Shared pytest fixtures for tests/api/."""
import pytest


@pytest.fixture(autouse=True)
def _reset_active_project():
    """Reset deps._active_project around each test (one-shot cache leaks otherwise)."""
    import flake_analysis.api.deps as deps
    deps._active_project = None
    yield
    deps._active_project = None
