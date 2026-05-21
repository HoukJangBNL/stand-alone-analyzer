"""Async SQLAlchemy engine and session factory."""
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from flake_analysis.db.url import get_db_url

engine = create_async_engine(
    get_db_url(),
    pool_size=5,
    max_overflow=5,
    pool_pre_ping=True,
)

async_session_maker = async_sessionmaker(engine, expire_on_commit=False)
