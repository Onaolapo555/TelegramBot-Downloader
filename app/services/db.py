from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        s = get_settings()
        # echo based on log level
        echo = s.log_level == "DEBUG"
        _engine = create_async_engine(s.database_url, echo=echo, pool_pre_ping=True)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


async def init_db() -> None:
    # Import models to register metadata
    import app.models.job
    import app.models.user  # noqa: F401

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Ensure indexes exist for existing DBs (lightweight & fast count_active_jobs / prune)
        try:
            from sqlalchemy import text

            # These are safe to run even if indexes already exist
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_jobs_user_status ON jobs (user_id, status)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_jobs_created_at ON jobs (created_at)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs (status)"))
            await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_jobs_user_id ON jobs (user_id)"))
        except Exception:
            # Ignore if DB doesn't support IF NOT EXISTS or already exists (e.g., SQLite create_all already did)
            pass


async def get_session() -> AsyncSession:  # type: ignore
    factory = get_session_factory()
    async with factory() as session:
        yield session  # type: ignore
