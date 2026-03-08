"""Persistent idempotency helpers for API gateway write endpoints."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any, Protocol, cast

from shared_kernel import DatabaseClient, DatabaseExecutor, ServiceError

_INSERT_SUCCESS_TAG = "INSERT 0 1"
_UPDATE_SINGLE_ROW_TAG = "UPDATE 1"
_STATUS_PENDING = "pending"
_STATUS_COMPLETED = "completed"
_STATUS_FAILED = "failed"


@dataclass(frozen=True, slots=True)
class IdempotencyReservation:
    """Reservation result for a request key."""

    replay_payload: dict[str, Any] | None
    replay_error: dict[str, Any] | None = None
    replay_status_code: int | None = None


class DatabasePort(Protocol):
    """Minimal async DB contract required by idempotency repository."""

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def execute(self, query: str, *args: Any) -> str: ...

    def transaction(self) -> AbstractAsyncContextManager[DatabaseExecutor]: ...


class GatewayIdempotencyRepository:
    """Persistence adapter for idempotency-key semantics."""

    def __init__(
        self,
        db: DatabasePort,
        *,
        ttl_seconds: int,
        stale_after_seconds: int,
    ) -> None:
        self._db = db
        self._ttl_seconds = ttl_seconds
        self._stale_after_seconds = stale_after_seconds

    @classmethod
    def from_dsn(
        cls,
        postgres_dsn: str,
        *,
        ttl_seconds: int,
        stale_after_seconds: int,
    ) -> GatewayIdempotencyRepository:
        return cls(
            DatabaseClient(postgres_dsn),
            ttl_seconds=ttl_seconds,
            stale_after_seconds=stale_after_seconds,
        )

    async def connect(self) -> None:
        await self._db.connect()

    async def close(self) -> None:
        await self._db.close()

    async def reserve_request(
        self,
        *,
        idempotency_key: str,
        endpoint: str,
        request_hash: str,
    ) -> IdempotencyReservation:
        async with self._db.transaction() as tx:
            insert_result = await tx.execute(
                """
                INSERT INTO idempotency_keys (
                    idempotency_key,
                    endpoint,
                    request_hash,
                    status,
                    locked_at,
                    expires_at,
                    created_at,
                    updated_at
                ) VALUES (
                    $1,
                    $2,
                    $3,
                    $4,
                    NOW(),
                    NOW() + ($5 * INTERVAL '1 second'),
                    NOW(),
                    NOW()
                )
                ON CONFLICT (idempotency_key) DO NOTHING
                """,
                idempotency_key,
                endpoint,
                request_hash,
                _STATUS_PENDING,
                self._ttl_seconds,
            )
            row = await tx.fetchrow(
                """
                SELECT
                    endpoint,
                    request_hash,
                    status,
                    response_payload,
                    response_status_code,
                    error_payload,
                    error_status_code,
                    locked_at,
                    expires_at
                FROM idempotency_keys
                WHERE idempotency_key = $1
                FOR UPDATE
                """,
                idempotency_key,
            )
            if row is None:
                raise ServiceError(
                    error_code="IDEMPOTENCY_LOOKUP_FAILED",
                    message="Idempotency key was not found after reservation attempt",
                    operation="idempotency_reserve",
                    status_code=500,
                    cause=idempotency_key,
                )
            record = cast(Mapping[str, object], row)
            stored_endpoint = cast(str, record["endpoint"])
            stored_hash = cast(str, record["request_hash"])
            stored_status = cast(str, record["status"])
            if stored_endpoint != endpoint:
                raise ServiceError(
                    error_code="IDEMPOTENCY_ENDPOINT_MISMATCH",
                    message="Idempotency key already exists for a different endpoint",
                    operation="idempotency_reserve",
                    status_code=409,
                    hint="Use a unique idempotency key per endpoint and request payload",
                )
            if stored_hash != request_hash:
                raise ServiceError(
                    error_code="IDEMPOTENCY_REQUEST_MISMATCH",
                    message="Idempotency key already exists for a different request payload",
                    operation="idempotency_reserve",
                    status_code=409,
                    hint="Reuse key only for exact request retries",
                )

            if stored_status == _STATUS_COMPLETED:
                stored_payload = _normalize_payload(record["response_payload"])
                if stored_payload is None:
                    raise ServiceError(
                        error_code="IDEMPOTENCY_PAYLOAD_INVALID",
                        message="Completed idempotency record is missing response payload",
                        operation="idempotency_reserve",
                        status_code=500,
                        cause=idempotency_key,
                    )
                return IdempotencyReservation(replay_payload=stored_payload)

            if stored_status == _STATUS_FAILED:
                stored_error = _normalize_payload(record["error_payload"])
                error_status_code = record["error_status_code"]
                if stored_error is not None and isinstance(error_status_code, int):
                    return IdempotencyReservation(
                        replay_payload=None,
                        replay_error=stored_error,
                        replay_status_code=error_status_code,
                    )

            if insert_result == _INSERT_SUCCESS_TAG:
                return IdempotencyReservation(replay_payload=None)

            refresh_result = await tx.execute(
                """
                UPDATE idempotency_keys
                SET
                    status = $2,
                    locked_at = NOW(),
                    expires_at = NOW() + ($3 * INTERVAL '1 second'),
                    response_payload = NULL,
                    response_status_code = NULL,
                    error_payload = NULL,
                    error_status_code = NULL,
                    updated_at = NOW()
                WHERE idempotency_key = $1
                  AND (
                    status = $4
                    OR expires_at <= NOW()
                    OR locked_at <= NOW() - ($5 * INTERVAL '1 second')
                  )
                """,
                idempotency_key,
                _STATUS_PENDING,
                self._ttl_seconds,
                _STATUS_FAILED,
                self._stale_after_seconds,
            )
            if refresh_result == _UPDATE_SINGLE_ROW_TAG:
                return IdempotencyReservation(replay_payload=None)

        raise ServiceError(
            error_code="IDEMPOTENCY_IN_PROGRESS",
            message="Request with this idempotency key is already being processed",
            operation="idempotency_reserve",
            status_code=409,
            hint="Retry with same key after the original request completes",
        )

    async def persist_response(
        self,
        *,
        idempotency_key: str,
        response_payload: Mapping[str, Any],
        response_status_code: int,
    ) -> None:
        update_result = await self._execute_finalization(
            idempotency_key=idempotency_key,
            status=_STATUS_COMPLETED,
            payload_column="response_payload",
            payload=dict(response_payload),
            status_code_column="response_status_code",
            status_code=response_status_code,
        )
        if update_result != _UPDATE_SINGLE_ROW_TAG:
            raise ServiceError(
                error_code="IDEMPOTENCY_PERSIST_FAILED",
                message="Failed to persist idempotency response payload",
                operation="idempotency_persist",
                status_code=500,
                cause=f"idempotency_key={idempotency_key}",
            )

    async def persist_failure(
        self,
        *,
        idempotency_key: str,
        error_payload: Mapping[str, Any],
        error_status_code: int,
    ) -> None:
        update_result = await self._execute_finalization(
            idempotency_key=idempotency_key,
            status=_STATUS_FAILED,
            payload_column="error_payload",
            payload=dict(error_payload),
            status_code_column="error_status_code",
            status_code=error_status_code,
        )
        if update_result != _UPDATE_SINGLE_ROW_TAG:
            raise ServiceError(
                error_code="IDEMPOTENCY_PERSIST_FAILED",
                message="Failed to persist idempotency error payload",
                operation="idempotency_persist_failure",
                status_code=500,
                cause=f"idempotency_key={idempotency_key}",
            )

    async def _execute_finalization(
        self,
        *,
        idempotency_key: str,
        status: str,
        payload_column: str,
        payload: Mapping[str, Any],
        status_code_column: str,
        status_code: int,
    ) -> str:
        query = _finalization_query(
            payload_column=payload_column,
            status_code_column=status_code_column,
        )
        return await self._db.execute(
            query,
            idempotency_key,
            status,
            json.dumps(payload),
            status_code,
            _STATUS_PENDING,
        )


def compute_request_hash(payload: Mapping[str, Any]) -> str:
    """Build deterministic request hash for idempotency comparisons."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_payload(raw_payload: object) -> dict[str, object] | None:
    if raw_payload is None:
        return None
    payload: object = raw_payload
    if isinstance(payload, str):
        payload = cast(object, json.loads(payload))
    if not isinstance(payload, Mapping):
        raise ServiceError(
            error_code="IDEMPOTENCY_PAYLOAD_INVALID",
            message="Stored idempotency payload has invalid type",
            operation="idempotency_normalize_payload",
            status_code=500,
            cause=type(payload).__name__,
        )
    payload_mapping = cast(Mapping[object, object], payload)
    return {str(key): value for key, value in payload_mapping.items()}


def _finalization_query(*, payload_column: str, status_code_column: str) -> str:
    queries = {
        ("response_payload", "response_status_code"): """
        UPDATE idempotency_keys
        SET
            status = $2,
            response_payload = $3::jsonb,
            response_status_code = $4,
            locked_at = NULL,
            completed_at = NOW(),
            updated_at = NOW()
        WHERE idempotency_key = $1
          AND status = $5
        """,
        ("error_payload", "error_status_code"): """
        UPDATE idempotency_keys
        SET
            status = $2,
            error_payload = $3::jsonb,
            error_status_code = $4,
            locked_at = NULL,
            completed_at = NOW(),
            updated_at = NOW()
        WHERE idempotency_key = $1
          AND status = $5
        """,
    }
    try:
        return queries[(payload_column, status_code_column)]
    except KeyError as exc:
        raise ValueError(
            "payload/status code columns must match supported idempotency payload fields"
        ) from exc
