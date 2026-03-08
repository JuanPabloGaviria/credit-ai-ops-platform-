from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any, cast

import asyncpg
import pytest

from shared_kernel import DatabaseClient, ServiceError


class _FakeTransactionContext:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        return None


class _FakeConnection:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.execute_result: str = "EXECUTE 1"
        self.fetch_result: list[asyncpg.Record] = []
        self.fetchrow_result: asyncpg.Record | None = None

    async def execute(self, query: str, *args: Any) -> str:
        self.executed.append((query, args))
        return self.execute_result

    async def fetch(self, query: str, *args: Any) -> Sequence[asyncpg.Record]:
        self.executed.append((query, args))
        return self.fetch_result

    async def fetchrow(self, query: str, *args: Any) -> asyncpg.Record | None:
        self.executed.append((query, args))
        return self.fetchrow_result

    def transaction(self) -> _FakeTransactionContext:
        return _FakeTransactionContext()


class _FakeAcquireContext:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> _FakeConnection:
        return self._connection

    async def __aexit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        return None


class _FakePool:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection
        self.closed = False

    def acquire(self) -> _FakeAcquireContext:
        return _FakeAcquireContext(self._connection)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.unit
def test_database_client_connect_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    created = {"count": 0}
    connection = _FakeConnection()
    pool = _FakePool(connection)

    async def _fake_create_pool(**_kwargs: Any) -> _FakePool:
        created["count"] += 1
        return pool

    monkeypatch.setattr("shared_kernel.database.asyncpg.create_pool", _fake_create_pool)
    client = DatabaseClient("postgresql://db.example:5432/credit_ai_ops")

    asyncio.run(client.connect())
    asyncio.run(client.connect())

    assert created["count"] == 1


@pytest.mark.unit
def test_database_client_connect_raises_typed_error_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _failing_create_pool(**_kwargs: Any) -> _FakePool:
        raise RuntimeError("db down")

    monkeypatch.setattr("shared_kernel.database.asyncpg.create_pool", _failing_create_pool)
    client = DatabaseClient("postgresql://db.example:5432/credit_ai_ops")

    with pytest.raises(ServiceError) as error:
        asyncio.run(client.connect())

    assert error.value.error_code == "DB_CONNECT_FAILED"


@pytest.mark.unit
def test_database_client_requires_pool_before_operations() -> None:
    client = DatabaseClient("postgresql://db.example:5432/credit_ai_ops")

    async def _scenario() -> None:
        with pytest.raises(ServiceError) as execute_error:
            await client.execute("SELECT 1")
        with pytest.raises(ServiceError) as fetch_error:
            await client.fetch("SELECT 1")
        with pytest.raises(ServiceError) as fetchrow_error:
            await client.fetchrow("SELECT 1")
        with pytest.raises(ServiceError) as transaction_error:
            async with client.transaction():
                pass

        assert execute_error.value.error_code == "DB_POOL_NOT_READY"
        assert fetch_error.value.error_code == "DB_POOL_NOT_READY"
        assert fetchrow_error.value.error_code == "DB_POOL_NOT_READY"
        assert transaction_error.value.error_code == "DB_POOL_NOT_READY"

    asyncio.run(_scenario())


@pytest.mark.unit
def test_database_client_execute_fetch_fetchrow_and_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _FakeConnection()
    connection.execute_result = "UPDATE 1"
    connection.fetch_result = [cast(asyncpg.Record, {"value": 1})]
    connection.fetchrow_result = cast(asyncpg.Record, {"value": 2})
    client = DatabaseClient("postgresql://db.example:5432/credit_ai_ops")
    fake_pool = _FakePool(connection)

    async def _fake_create_pool(**_kwargs: Any) -> _FakePool:
        return fake_pool

    monkeypatch.setattr("shared_kernel.database.asyncpg.create_pool", _fake_create_pool)

    async def _scenario() -> None:
        await client.connect()
        execute_result = await client.execute("UPDATE table SET value = $1", 1)
        fetch_result = await client.fetch("SELECT value FROM table")
        fetchrow_result = await client.fetchrow("SELECT value FROM table LIMIT 1")
        await client.close()

        assert execute_result == "UPDATE 1"
        assert len(fetch_result) == 1
        assert fetchrow_result is not None
        assert fetchrow_result["value"] == 2
        assert fake_pool.closed is True
        with pytest.raises(ServiceError) as error:
            await client.execute("SELECT 1")
        assert error.value.error_code == "DB_POOL_NOT_READY"

    asyncio.run(_scenario())


@pytest.mark.unit
def test_database_client_transaction_yields_connection_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _FakeConnection()
    connection.execute_result = "INSERT 1"
    connection.fetch_result = [cast(asyncpg.Record, {"id": "row-1"})]
    connection.fetchrow_result = cast(asyncpg.Record, {"id": "row-2"})
    client = DatabaseClient("postgresql://db.example:5432/credit_ai_ops")
    fake_pool = _FakePool(connection)

    async def _fake_create_pool(**_kwargs: Any) -> _FakePool:
        return fake_pool

    monkeypatch.setattr("shared_kernel.database.asyncpg.create_pool", _fake_create_pool)

    async def _scenario() -> None:
        await client.connect()
        async with client.transaction() as tx:
            execute_result = await tx.execute("INSERT INTO table VALUES ($1)", "x")
            fetched = await tx.fetch("SELECT id FROM table")
            fetched_row = await tx.fetchrow("SELECT id FROM table LIMIT 1")

        assert execute_result == "INSERT 1"
        assert len(fetched) == 1
        assert fetched_row is not None
        assert fetched_row["id"] == "row-2"
        await client.close()

    asyncio.run(_scenario())
