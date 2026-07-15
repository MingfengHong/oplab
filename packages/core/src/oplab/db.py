from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from oplab.config import Settings
from oplab.domain.models import Base


class Database:
    def __init__(self, settings: Settings):
        connect_args = (
            {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
        )
        self.engine: AsyncEngine = create_async_engine(
            settings.database_url,
            pool_pre_ping=True,
            connect_args=connect_args,
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as session:
            yield session
