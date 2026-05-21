"""Fixtures for tests/scripts/.

These tests exercise the alembic drift helper directly against a
writable PostgreSQL. Set ``SAA_TEST_DATABASE_URL`` to an asyncpg URL to
enable; otherwise tests are skipped (same gating as ``tests/db``).

Unlike ``tests/db``, the drift tests do schema-level operations
(``create_all``, ``CREATE TABLE rogue``) outside any rollback wrapper, so
the fixture cleans up by dropping all tables on teardown.
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
    """Per-test AsyncEngine with full table cleanup on teardown."""
    if not _TEST_URL:
        pytest.skip(
            "SAA_TEST_DATABASE_URL not set; tests/scripts requires a "
            "writable PostgreSQL (drop privileges required)."
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
