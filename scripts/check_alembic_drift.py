"""Compare alembic-applied schema to SQLAlchemy ORM metadata.

Exit 0 on clean (no drift), 1 on drift. Used by CI.
"""
from __future__ import annotations
import asyncio
import sys
from typing import Any

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from flake_analysis.db import Base, get_db_url


async def compute_drift(engine: AsyncEngine, metadata) -> list[Any]:
    """Return non-empty list when DB schema diverges from metadata."""
    def _compare(sync_conn) -> list[Any]:
        ctx = MigrationContext.configure(sync_conn)
        return list(compare_metadata(ctx, metadata))

    async with engine.connect() as conn:
        return await conn.run_sync(_compare)


async def main() -> int:
    engine = create_async_engine(get_db_url(async_driver=True))
    try:
        diffs = await compute_drift(engine, Base.metadata)
    finally:
        await engine.dispose()

    if not diffs:
        print("alembic drift check: CLEAN")
        return 0

    print("alembic drift check: DRIFT DETECTED")
    for op in diffs:
        print(f"  {op!r}")
        # GH Actions annotation
        print(f"::error::alembic drift: {op!r}")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
