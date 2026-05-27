"""Fixtures for tests/scripts/.

These tests exercise the alembic drift helper directly against a
writable PostgreSQL. Set ``SAA_TEST_DATABASE_URL`` to an asyncpg URL to
enable; otherwise tests are skipped (same gating as ``tests/db``).

Unlike ``tests/db``, the drift tests do schema-level operations
(``create_all``, ``CREATE TABLE rogue``) outside any rollback wrapper, so
the fixture cleans up by dropping all tables on teardown.

Destructive teardown opt-in (issue #65)
---------------------------------------
The teardown calls ``Base.metadata.drop_all`` against whatever DB
``SAA_TEST_DATABASE_URL`` points at. When that URL points at the same DB
the rest of the suite uses (e.g. ``saa_test``), the drop wipes the schema
mid-suite and cascades into 100+ failures in the wider gate. We can't
robustly auto-detect "is this a dedicated scripts DB?", so we require an
explicit opt-in: set ``SAA_SCRIPTS_DESTRUCTIVE=1`` to enable the fixture.
Without it, the fixture skips before yielding, which means the two
``test_compute_drift_*`` tests are skipped during the local acceptance
gate. The real drift coverage path is the dedicated CI workflow at
``.github/workflows/alembic-drift.yml``, which provisions an isolated DB
and sets ``SAA_SCRIPTS_DESTRUCTIVE=1``.
"""
from __future__ import annotations

import os
from typing import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

_TEST_URL = os.environ.get("SAA_TEST_DATABASE_URL")


@pytest_asyncio.fixture()
async def pg_engine() -> AsyncIterator[AsyncEngine]:
    """Per-test AsyncEngine with full table cleanup on teardown.

    Skipped unless ``SAA_SCRIPTS_DESTRUCTIVE=1`` is set — the teardown
    drops all tables and would corrupt the shared ``saa_test`` DB used by
    the rest of the suite. See module docstring for the opt-in rationale
    (issue #65).
    """
    if not _TEST_URL:
        pytest.skip(
            "SAA_TEST_DATABASE_URL not set; tests/scripts requires a "
            "writable PostgreSQL (drop privileges required)."
        )

    if os.environ.get("SAA_SCRIPTS_DESTRUCTIVE") != "1":
        pytest.skip(
            "tests/scripts pg_engine teardown drops all tables; opt in by "
            "setting SAA_SCRIPTS_DESTRUCTIVE=1 against an isolated DB. "
            "See tests/scripts/conftest.py docstring (issue #65)."
        )

    engine = create_async_engine(_TEST_URL, future=True)
    try:
        yield engine
    finally:
        # Drop everything the test created so the next test starts clean.
        # We import Base lazily because tests may have mutated metadata.
        from flake_analysis.db import Base
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
                # Also drop any rogue tables a test may have created
                # outside Base.metadata (e.g., raw CREATE TABLE).
                await conn.execute(text("DROP TABLE IF EXISTS rogue CASCADE"))
        finally:
            await engine.dispose()
