"""Audit persistence and retrieval adapters."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from contracts import EventEnvelope
from security import redact_pii
from shared_kernel import DatabaseClient, ServiceError, record_inbox_event


@dataclass(frozen=True, slots=True)
class AuditEventRecord:
    """Typed read model for persisted audit events."""

    event_id: str
    event_name: str
    payload: dict[str, Any]
    trace_id: str | None
    correlation_id: str
    causation_id: str | None
    created_at: datetime


class AuditRepository:
    """Audit repository consuming all event streams with PII redaction."""

    def __init__(self, postgres_dsn: str) -> None:
        self._db = DatabaseClient(postgres_dsn)

    async def connect(self) -> None:
        await self._db.connect()

    async def close(self) -> None:
        await self._db.close()

    async def handle_event(self, event: EventEnvelope) -> bool:
        async with self._db.transaction() as tx:
            first_seen = await record_inbox_event(tx, "audit_inbox", event)
            if not first_seen:
                return False

            safe_payload = redact_pii(event.payload)
            await tx.execute(
                """
                INSERT INTO audit_events (
                    event_id,
                    event_name,
                    payload,
                    trace_id,
                    correlation_id,
                    causation_id,
                    created_at
                ) VALUES ($1, $2, $3::jsonb, $4, $5, $6, NOW())
                ON CONFLICT (event_id) DO NOTHING
                """,
                event.event_id,
                event.event_name,
                json.dumps(safe_payload),
                event.trace_id,
                event.correlation_id,
                event.causation_id,
            )
        return True

    async def list_events(
        self,
        *,
        event_name: str | None,
        trace_id: str | None,
        correlation_id: str | None,
        limit: int,
    ) -> list[AuditEventRecord]:
        records = await self._db.fetch(
            """
            SELECT event_id, event_name, payload, trace_id, correlation_id, causation_id, created_at
            FROM audit_events
            WHERE ($1::text IS NULL OR event_name = $1)
              AND ($2::text IS NULL OR trace_id = $2)
              AND ($3::text IS NULL OR correlation_id = $3)
            ORDER BY created_at DESC
            LIMIT $4
            """,
            event_name,
            trace_id,
            correlation_id,
            limit,
        )
        return [self._to_audit_event_record(record) for record in records]

    async def get_event(self, event_id: str) -> AuditEventRecord | None:
        record = await self._db.fetchrow(
            """
            SELECT event_id, event_name, payload, trace_id, correlation_id, causation_id, created_at
            FROM audit_events
            WHERE event_id = $1
            """,
            event_id,
        )
        if record is None:
            return None
        return self._to_audit_event_record(record)

    def _to_audit_event_record(self, record: Mapping[str, object]) -> AuditEventRecord:
        created_at = record.get("created_at")
        if not isinstance(created_at, datetime):
            raise ServiceError(
                error_code="AUDIT_EVENT_INVALID_TIMESTAMP",
                message="Audit event row has invalid created_at value",
                operation="audit_to_read_model",
                status_code=500,
                cause=str(type(created_at)),
            )

        trace_id_value = record.get("trace_id")
        if trace_id_value is not None and not isinstance(trace_id_value, str):
            raise ServiceError(
                error_code="AUDIT_EVENT_INVALID_TRACE",
                message="Audit event row has invalid trace_id value",
                operation="audit_to_read_model",
                status_code=500,
                cause=str(type(trace_id_value)),
            )
        trace_id = trace_id_value if isinstance(trace_id_value, str) else None

        correlation_id_value = record.get("correlation_id")
        if not isinstance(correlation_id_value, str):
            raise ServiceError(
                error_code="AUDIT_EVENT_INVALID_CORRELATION",
                message="Audit event row has invalid correlation_id value",
                operation="audit_to_read_model",
                status_code=500,
                cause=str(type(correlation_id_value)),
            )

        causation_id_value = record.get("causation_id")
        if causation_id_value is not None and not isinstance(causation_id_value, str):
            raise ServiceError(
                error_code="AUDIT_EVENT_INVALID_CAUSATION",
                message="Audit event row has invalid causation_id value",
                operation="audit_to_read_model",
                status_code=500,
                cause=str(type(causation_id_value)),
            )
        causation_id = causation_id_value if isinstance(causation_id_value, str) else None

        return AuditEventRecord(
            event_id=self._require_string(record, key="event_id"),
            event_name=self._require_string(record, key="event_name"),
            payload=self._normalize_payload(record.get("payload")),
            trace_id=trace_id,
            correlation_id=correlation_id_value,
            causation_id=causation_id,
            created_at=created_at,
        )

    def _normalize_payload(self, raw_payload: object) -> dict[str, Any]:
        payload: object = raw_payload
        if isinstance(payload, bytes | bytearray):
            payload = payload.decode("utf-8")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise ServiceError(
                    error_code="AUDIT_PAYLOAD_INVALID_JSON",
                    message="Persisted audit payload is not valid JSON",
                    operation="audit_normalize_payload",
                    status_code=500,
                    cause=str(exc),
                ) from exc

        if not isinstance(payload, Mapping):
            raise ServiceError(
                error_code="AUDIT_PAYLOAD_INVALID_TYPE",
                message="Persisted audit payload is not an object",
                operation="audit_normalize_payload",
                status_code=500,
                cause=str(type(payload)),
            )

        payload_mapping = cast(Mapping[object, object], payload)
        payload_dict: dict[str, Any] = {}
        for key_obj, value_obj in payload_mapping.items():
            if not isinstance(key_obj, str):
                raise ServiceError(
                    error_code="AUDIT_PAYLOAD_INVALID_KEY",
                    message="Persisted audit payload has non-string key",
                    operation="audit_normalize_payload",
                    status_code=500,
                    cause=str(type(key_obj)),
                )
            payload_dict[key_obj] = cast(Any, value_obj)

        return redact_pii(payload_dict)

    def _require_string(self, record: Mapping[str, object], *, key: str) -> str:
        value = record.get(key)
        if not isinstance(value, str):
            raise ServiceError(
                error_code="AUDIT_EVENT_INVALID_VALUE",
                message="Audit event row has invalid field type",
                operation="audit_to_read_model",
                status_code=500,
                cause=f"field={key} type={type(value)}",
            )
        return value
