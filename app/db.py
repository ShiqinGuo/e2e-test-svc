from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import Settings


class Database:
    def __init__(self, settings: Settings):
        connect_args = {}
        if settings.database_schema:
            if not settings.database_schema.replace("_", "").isalnum():
                raise ValueError("Invalid database schema")
            connect_args = {"server_settings": {"search_path": settings.database_schema}}
        self.engine = create_async_engine(settings.database_url, pool_pre_ping=True, connect_args=connect_args)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def close(self):
        await self.engine.dispose()


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.context.db.sessions() as session:
        yield session
