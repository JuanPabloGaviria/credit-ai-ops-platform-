from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from typing import Any, cast

import pytest
from api_gateway.idempotency import DatabasePort, GatewayIdempotencyRepository, compute_request_hash

from shared_kernel import ServiceError


class _FakeTransaction:
    def __init__(self, db: _FakeDatabaseClient) -> None:
        self._db = db

    async def execute(self, query: str, *args: Any) -> str:
        return await self._db.execute(query, *args)

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        _ = (query, args)
        return []

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        return await self._db.fetchrow(query, *args)


class _FakeTransactionContext:
    def __init__(self, tx: _FakeTransaction) -> None:
        self._tx = tx

    async def __aenter__(self) -> _FakeTransaction:
        return self._tx

    async def __aexit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        return None


class _FakeDatabaseClient:
    def __init__(
        self,
        *,
        insert_result: str = "INSERT 0 1",
        row: dict[str, Any] | None = None,
        update_result: str = "UPDATE 1",
        refresh_result: str = "UPDATE 0",
    ) -> None:
        self.insert_result = insert_result
        self.row = row
        self.update_result = update_result
        self.refresh_result = refresh_result
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.fetched: list[tuple[str, tuple[Any, ...]]] = []

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def execute(self, query: str, *args: Any) -> str:
        self.executed.append((query, args))
        if "INSERT INTO idempotency_keys" in query:
            return self.insert_result
        if "UPDATE idempotency_keys" in query and "completed_at = NOW()" in query:
            return self.update_result
        if "UPDATE idempotency_keys" in query and "expires_at = NOW() +" in query:
            return self.refresh_result
        return "OK"

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        self.fetched.append((query, args))
        return self.row

    def transaction(self) -> AbstractAsyncContextManager[_FakeTransaction]:
        return cast(
            AbstractAsyncContextManager[_FakeTransaction],
            _FakeTransactionContext(_FakeTransaction(self)),
        )


def _repository(fake_db: _FakeDatabaseClient) -> GatewayIdempotencyRepository:
    return GatewayIdempotencyRepository(
        cast(DatabasePort, fake_db),
        ttl_seconds=300,
        stale_after_seconds=120,
    )


def _base_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "endpoint": "/v1/gateway/credit-evaluate",
        "request_hash": "hash-1",
        "status": "pending",
        "response_payload": None,
        "response_status_code": None,
        "error_payload": None,
        "error_status_code": None,
        "locked_at": None,
        "expires_at": None,
    }
    row.update(overrides)
    return row


@pytest.mark.unit
def test_compute_request_hash_is_deterministic() -> None:
    payload_a = {"monthly_income": 5000, "existing_defaults": 0}
    payload_b = {"existing_defaults": 0, "monthly_income": 5000}

    assert compute_request_hash(payload_a) == compute_request_hash(payload_b)


@pytest.mark.unit
def test_reserve_request_returns_new_reservation() -> None:
    async def scenario() -> None:
        fake_db = _FakeDatabaseClient(
            insert_result="INSERT 0 1",
            row=_base_row(),
        )
        reservation = await _repository(fake_db).reserve_request(
            idempotency_key="idem-0001",
            endpoint="/v1/gateway/credit-evaluate",
            request_hash="hash-1",
        )

        assert reservation.replay_payload is None

    asyncio.run(scenario())


@pytest.mark.unit
def test_reserve_request_returns_replay_payload() -> None:
    async def scenario() -> None:
        replay_payload = {"score": {"risk_score": 0.32}}
        fake_db = _FakeDatabaseClient(
            insert_result="INSERT 0 0",
            row=_base_row(
                status="completed",
                response_payload=replay_payload,
                response_status_code=200,
            ),
        )
        reservation = await _repository(fake_db).reserve_request(
            idempotency_key="idem-0001",
            endpoint="/v1/gateway/credit-evaluate",
            request_hash="hash-1",
        )

        assert reservation.replay_payload == replay_payload
        assert reservation.replay_error is None

    asyncio.run(scenario())


@pytest.mark.unit
def test_reserve_request_returns_replay_error_payload() -> None:
    async def scenario() -> None:
        replay_error = {"error_code": "DOWNSTREAM_UNAVAILABLE"}
        fake_db = _FakeDatabaseClient(
            insert_result="INSERT 0 0",
            row=_base_row(
                status="failed",
                error_payload=replay_error,
                error_status_code=503,
            ),
        )
        reservation = await _repository(fake_db).reserve_request(
            idempotency_key="idem-0001",
            endpoint="/v1/gateway/credit-evaluate",
            request_hash="hash-1",
        )

        assert reservation.replay_payload is None
        assert reservation.replay_error == replay_error
        assert reservation.replay_status_code == 503

    asyncio.run(scenario())


@pytest.mark.unit
def test_reserve_request_rejects_mismatched_payload_hash() -> None:
    async def scenario() -> None:
        fake_db = _FakeDatabaseClient(
            insert_result="INSERT 0 0",
            row=_base_row(request_hash="hash-original"),
        )

        with pytest.raises(ServiceError) as error:
            await _repository(fake_db).reserve_request(
                idempotency_key="idem-0001",
                endpoint="/v1/gateway/credit-evaluate",
                request_hash="hash-changed",
            )
        assert error.value.error_code == "IDEMPOTENCY_REQUEST_MISMATCH"

    asyncio.run(scenario())


@pytest.mark.unit
def test_reserve_request_rejects_in_progress_key() -> None:
    async def scenario() -> None:
        fake_db = _FakeDatabaseClient(
            insert_result="INSERT 0 0",
            row=_base_row(status="pending"),
            refresh_result="UPDATE 0",
        )

        with pytest.raises(ServiceError) as error:
            await _repository(fake_db).reserve_request(
                idempotency_key="idem-0001",
                endpoint="/v1/gateway/credit-evaluate",
                request_hash="hash-1",
            )
        assert error.value.error_code == "IDEMPOTENCY_IN_PROGRESS"

    asyncio.run(scenario())


@pytest.mark.unit
def test_persist_response_requires_single_row_update() -> None:
    async def scenario() -> None:
        fake_db = _FakeDatabaseClient(update_result="UPDATE 0")

        with pytest.raises(ServiceError) as error:
            await _repository(fake_db).persist_response(
                idempotency_key="idem-0001",
                response_payload={"decision": {"decision": "approve"}},
                response_status_code=200,
            )
        assert error.value.error_code == "IDEMPOTENCY_PERSIST_FAILED"

    asyncio.run(scenario())
