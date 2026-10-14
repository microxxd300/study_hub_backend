import logging
from collections.abc import AsyncIterator
from contextvars import ContextVar

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

log = logging.getLogger("studyhub.db")

# Per-request query counter (N+1 guard). None = not counting.
query_counter: ContextVar[list[int] | None] = ContextVar("query_counter", default=None)


def _connect_args(url: str) -> dict:
    # Supabase transaction pooler (port 6543 / pgbouncer) can't use prepared statements.
    if make_url(url).port == 6543:
        return {"statement_cache_size": 0, "prepared_statement_cache_size": 0}
    return {}


def build_engine(url: str | None = None) -> AsyncEngine:
    settings = get_settings()
    url = url or settings.database_url
    engine = create_async_engine(
        url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args=_connect_args(url),
    )
    if not settings.is_production:
        install_query_counter(engine)
    return engine


def install_query_counter(engine: AsyncEngine) -> None:
    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def _count(conn, cursor, statement, parameters, context, executemany):
        counter = query_counter.get()
        if counter is not None:
            counter[0] += 1


engine = build_engine()
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
