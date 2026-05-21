"""Shared fixtures for tests/db/.

These tests require a writable PostgreSQL with the v6 schema applied.
Set SAA_TEST_DATABASE_URL to an asyncpg URL (e.g.
``postgresql+asyncpg://user:pw@localhost:5432/qpress_test``) to enable.
Otherwise every test in this directory is skipped.

Each test runs inside a SAVEPOINT-style transaction and is rolled back at
teardown so we never leave rows behind.
"""
from __future__ import annotations

import os
from typing import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_TEST_URL = os.environ.get("SAA_TEST_DATABASE_URL")


@pytest.fixture(scope="session")
def pg_url() -> str:
    if not _TEST_URL:
        pytest.skip(
            "SAA_TEST_DATABASE_URL not set; tests/db requires a writable "
            "PostgreSQL with the v6 schema applied (alembic upgrade head)."
        )
    return _TEST_URL


@pytest_asyncio.fixture()
async def pg_session(pg_url: str) -> AsyncIterator[AsyncSession]:
    """Per-test async session wrapped in a transaction that is rolled back."""
    engine = create_async_engine(pg_url, future=True)
    async with engine.connect() as conn:
        trans = await conn.begin()
        Session = async_sessionmaker(bind=conn, expire_on_commit=False)
        async with Session() as session:
            try:
                yield session
            finally:
                await session.close()
        await trans.rollback()
    await engine.dispose()
