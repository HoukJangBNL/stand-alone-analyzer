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
from datetime import datetime, timezone
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
    """Per-test async session wrapped in a transaction that is rolled back.

    Uses ``join_transaction_mode="create_savepoint"`` so any ``session.commit()``
    issued by the test (or by route handlers under test) is converted into a
    SAVEPOINT release rather than committing the outer transaction. This keeps
    the per-test rollback effective even when the code under test commits
    explicitly — preventing rows from leaking into ``saa_test`` across runs
    (e.g. the ``usage_events`` accumulation that previously broke
    ``tests/api/test_admin_usage_route.py``).
    """
    engine = create_async_engine(pg_url, future=True)
    async with engine.connect() as conn:
        trans = await conn.begin()
        Session = async_sessionmaker(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        async with Session() as session:
            try:
                yield session
            finally:
                await session.close()
        await trans.rollback()
    await engine.dispose()


@pytest_asyncio.fixture()
async def sample_analysis_factory(pg_session, sample_user_factory):
    """Insert a User + Project + Scan + Model + Analysis row and return
    the Analysis.

    No-arg call (`await sample_analysis_factory()`) auto-creates a fresh
    User + Project so callers don't need to wire ownership manually.
    Pass ``project=`` to scope the Analysis's Scan to an existing Project
    (mirrors ``sample_scan_factory`` in tests/api/conftest.py).

    W10-A made ``scans.project_id`` a NOT NULL FK→projects.id RESTRICT;
    constructing a Scan without a project_id would raise IntegrityError.
    See issue #172 (factory-level fix; gate fix #2 / commit fc173c4 only
    patched two call sites at the test level).

    Uses ``flush`` + ``refresh`` instead of ``commit`` so the per-test
    rollback in ``pg_session`` still cleans up. ``Analysis.status`` is a
    GENERATED column populated via RETURNING on flush.
    """
    from flake_analysis.db.models import Analysis, Model, Project, Scan

    counter = {"n": 0}

    async def _make(
        steps_done: dict | None = None,
        *,
        project: "Project | None" = None,
    ) -> "Analysis":
        counter["n"] += 1
        suffix = counter["n"]
        if project is None:
            owner = await sample_user_factory()
            project = Project(
                name=f"test-project-{suffix}", owner_id=owner.id
            )
            pg_session.add(project)
            await pg_session.flush()
            await pg_session.refresh(project)
        m = Model(
            name=f"test-model-{suffix}",
            base_model="sam2",
            s3_uri=f"s3://test/{suffix}",
        )
        pg_session.add(m)
        await pg_session.flush()
        # `material` is NOT NULL + FK→materials(name); use seeded "graphene".
        # `project_id` is NOT NULL + FK→projects.id (W10-A).
        s = Scan(
            name=f"test-scan-{suffix}",
            material="graphene",
            project_id=project.id,
        )
        pg_session.add(s)
        await pg_session.flush()
        a = Analysis(
            scan_id=s.id,
            model_id=m.id,
            amg_params={},
            link_distance_px=10.0,
            steps_done=steps_done or {},
        )
        pg_session.add(a)
        await pg_session.flush()
        await pg_session.refresh(a)
        return a

    return _make


@pytest_asyncio.fixture()
async def sample_user_factory(pg_session):
    """Insert a User row and return the User model.

    Uses ``flush`` + ``refresh`` instead of ``commit`` so the per-test
    rollback in ``pg_session`` still cleans up.
    """
    from flake_analysis.db.models import User, UserRole

    counter = {"n": 0}

    async def _make(
        email: str | None = None,
        role: UserRole = UserRole.MEMBER,
        cognito_sub: str | None = None,
    ) -> "User":
        counter["n"] += 1
        suffix = counter["n"]
        u = User(
            email=email or f"test-user-{suffix}@example.com",
            cognito_sub=cognito_sub or f"test-cognito-sub-{suffix}",
            role=role,
            email_verified_at=datetime.now(timezone.utc),
        )
        pg_session.add(u)
        await pg_session.flush()
        await pg_session.refresh(u)
        return u

    return _make
