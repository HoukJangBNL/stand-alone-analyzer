"""Shared pytest fixtures for tests/worker/.

Re-exports the PG fixtures from tests/db/conftest.py so ``@pytest.mark.pg``
tests under tests/worker/ can use ``pg_session``. pytest only auto-discovers
conftest along the rootdir->test_file path; tests/db/conftest.py is on a
sibling branch. Same pattern as tests/api/conftest.py.
"""
from tests.db.conftest import (  # noqa: F401
    pg_session,
    pg_url,
)
