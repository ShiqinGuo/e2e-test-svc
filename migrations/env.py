import asyncio

from alembic import context

from app.config import Settings
from app.db import Database
from app.models import Base

config = context.config
settings = config.attributes.get("settings") or Settings()


def run(connection):
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def online():
    database = Database(settings)
    async with database.engine.connect() as connection:
        await connection.run_sync(run)
    await database.close()


if context.is_offline_mode():
    context.configure(url=settings.database_url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(online())
