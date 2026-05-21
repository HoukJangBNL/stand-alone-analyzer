"""Shared pytest fixtures for tests/api/."""
import pytest

# Re-export PG fixtures from tests/db/conftest.py so pytest.mark.pg tests
# under tests/api/ can use them. pytest only auto-discovers conftest along
# the rootdir->test_file path; tests/db/conftest.py is on a sibling branch.
from tests.db.conftest import (  # noqa: F401
    pg_session,
    pg_url,
    sample_analysis_factory,
)


@pytest.fixture(autouse=True)
def _reset_active_project():
    """Reset deps._active_project around each test (one-shot cache leaks otherwise)."""
    import flake_analysis.api.deps as deps
    deps._active_project = None
    yield
    deps._active_project = None
