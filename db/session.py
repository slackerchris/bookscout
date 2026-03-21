"""
Async SQLAlchemy engine and session factory.

DATABASE_URL must be a postgresql+asyncpg:// connection string.
Falls back to env var DATABASE_URL, then a local default.
"""
import os
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://bookscout:bookscout@localhost/bookscout",
)

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)

AsyncSessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def get_session() -> AsyncSession:
    """FastAPI dependency — yields an async session and closes it when done."""
    async with AsyncSessionFactory() as session:
        yield session
