"""Async database client for service-local persistence adapters."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any, Protocol, cast

import asyncpg

from .errors import ServiceError


class DatabaseExecutor(Protocol):
    """Minimal async execution contract for db clients and transactions."""

    async def execute(self, query: str, *args: Any) -> str: ...

    async def fetch(self, query: str, *args: Any) -> Sequence[asyncpg.Record]: ...

    async def fetchrow(self, query: str, *args: Any) -> asyncpg.Record | None: ...


class _ConnectionExecutor(DatabaseExecutor):
    """Connection-bound executor used inside explicit transactions."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    async def execute(self, query: str, *args: Any) -> str:
        result = await self._connection.execute(query, *args)
        return cast(str, result)

    async def fetch(self, query: str, *args: Any) -> Sequence[asyncpg.Record]:
        records = await self._connection.fetch(query, *args)
        return cast(Sequence[asyncpg.Record], records)

    async def fetchrow(self, query: str, *args: Any) -> asyncpg.Record | None:
        row = await self._connection.fetchrow(query, *args)
        return cast(asyncpg.Record | None, row)


class DatabaseClient:
    """Lightweight asyncpg wrapper with explicit lifecycle methods."""

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def connect(self) -> None:
        if self._pool is None:
            try:
                self._pool = await asyncpg.create_pool(dsn=self._dsn, min_size=1, max_size=5)
            except Exception as exc:
                raise ServiceError(
                    error_code="DB_CONNECT_FAILED",
                    message="Failed to establish database pool",
                    operation="database_connect",
                    status_code=503,
                    cause=str(exc),
                    hint="Validate POSTGRES_DSN and database availability",
                ) from exc

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def execute(self, query: str, *args: Any) -> str:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(query, *args)
            return result

    async def fetch(self, query: str, *args: Any) -> Sequence[asyncpg.Record]:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            records = await conn.fetch(query, *args)
            return cast(Sequence[asyncpg.Record], records)

    async def fetchrow(self, query: str, *args: Any) -> asyncpg.Record | None:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(query, *args)
            return row

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[DatabaseExecutor]:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            connection = cast(Any, conn)
            async with connection.transaction():
                yield _ConnectionExecutor(connection)

    def _require_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise ServiceError(
                error_code="DB_POOL_NOT_READY",
                message="Database pool is not initialized",
                operation="database_use",
                status_code=500,
                hint="Call connect() before executing database operations",
            )
        return self._pool
