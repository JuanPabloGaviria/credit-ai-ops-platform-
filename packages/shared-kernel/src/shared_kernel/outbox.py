"""Outbox/inbox persistence helpers for event-driven flow."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Never, cast
from uuid import uuid4

from pydantic import ValidationError

from contracts import EventEnvelope

from .database import DatabaseExecutor
from .errors import ServiceError

_OUTBOX_TABLES = frozenset(
    {
        "application_outbox",
        "feature_outbox",
        "scoring_outbox",
        "decision_outbox",
        "assistant_outbox",
        "mlops_outbox",
    }
)
_INBOX_TABLES = frozenset(
    {
        "application_inbox",
        "feature_inbox",
        "scoring_inbox",
        "decision_inbox",
        "assistant_inbox",
        "audit_inbox",
    }
)
_UPDATE_SINGLE_ROW_TAG = "UPDATE 1"


@dataclass(frozen=True, slots=True)
class ClaimedOutboxEvent:
    """Claimed outbox event plus claim token for lease-safe completion."""

    event: EventEnvelope
    claim_token: str
    publish_attempts: int


def _normalize_payload(
    raw_payload: object,
    *,
    table_name: str,
    operation: str,
) -> dict[str, Any]:
    """Normalize JSONB payload returned by asyncpg into a dictionary."""
    payload: object = raw_payload
    if isinstance(payload, bytes | bytearray):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ServiceError(
                error_code="OUTBOX_PAYLOAD_INVALID_JSON",
                message="Outbox payload is not valid JSON",
                operation=operation,
                status_code=500,
                cause=str(exc),
                hint=f"Verify payload serialization for table '{table_name}'",
            ) from exc
    if isinstance(payload, dict):
        payload_dict = cast(dict[object, Any], payload)
        normalized_payload: dict[str, Any] = {}
        for key, value in payload_dict.items():
            if not isinstance(key, str):
                raise ServiceError(
                    error_code="OUTBOX_PAYLOAD_INVALID_TYPE",
                    message="Outbox payload has non-string key",
                    operation=operation,
                    status_code=500,
                    cause=type(key).__name__,
                    hint=f"Expected JSON object payload for table '{table_name}'",
                )
            normalized_payload[key] = value
        return normalized_payload
    if isinstance(payload, Mapping):
        raise ServiceError(
            error_code="OUTBOX_PAYLOAD_INVALID_TYPE",
            message="Outbox payload mapping must be a dictionary instance",
            operation=operation,
            status_code=500,
            cause="mapping_non_dict",
            hint=f"Expected JSON object payload for table '{table_name}'",
        )
    raise ServiceError(
        error_code="OUTBOX_PAYLOAD_INVALID_TYPE",
        message="Outbox payload has unsupported type",
        operation=operation,
        status_code=500,
        cause=type(payload).__name__,
        hint=f"Expected JSON object payload for table '{table_name}'",
    )


def _resolve_table(table_name: str, *, operation: str, table_set: frozenset[str]) -> str:
    if table_name not in table_set:
        raise ServiceError(
            error_code="UNSUPPORTED_OUTBOX_TABLE",
            message="Unsupported outbox/inbox table",
            operation=operation,
            status_code=500,
            cause=table_name,
            hint="Use one of the known outbox/inbox table constants",
        )
    return table_name


def _outbox_insert_query(table_name: str) -> str:
    match table_name:
        case "application_outbox":
            return """
            INSERT INTO application_outbox
            (event_id, event_name, payload, created_at, updated_at)
            VALUES ($1, $2, $3::jsonb, NOW(), NOW())
            ON CONFLICT (event_id) DO NOTHING
            """
        case "feature_outbox":
            return """
            INSERT INTO feature_outbox
            (event_id, event_name, payload, created_at, updated_at)
            VALUES ($1, $2, $3::jsonb, NOW(), NOW())
            ON CONFLICT (event_id) DO NOTHING
            """
        case "scoring_outbox":
            return """
            INSERT INTO scoring_outbox
            (event_id, event_name, payload, created_at, updated_at)
            VALUES ($1, $2, $3::jsonb, NOW(), NOW())
            ON CONFLICT (event_id) DO NOTHING
            """
        case "decision_outbox":
            return """
            INSERT INTO decision_outbox
            (event_id, event_name, payload, created_at, updated_at)
            VALUES ($1, $2, $3::jsonb, NOW(), NOW())
            ON CONFLICT (event_id) DO NOTHING
            """
        case "assistant_outbox":
            return """
            INSERT INTO assistant_outbox
            (event_id, event_name, payload, created_at, updated_at)
            VALUES ($1, $2, $3::jsonb, NOW(), NOW())
            ON CONFLICT (event_id) DO NOTHING
            """
        case "mlops_outbox":
            return """
            INSERT INTO mlops_outbox
            (event_id, event_name, payload, created_at, updated_at)
            VALUES ($1, $2, $3::jsonb, NOW(), NOW())
            ON CONFLICT (event_id) DO NOTHING
            """
        case _:
            return _unsupported_validated_table(table_name)


def _outbox_claim_query(table_name: str) -> str:
    match table_name:
        case "application_outbox":
            return """
            WITH next_events AS (
                SELECT event_id
                FROM application_outbox
                WHERE published_at IS NULL
                  AND dead_lettered_at IS NULL
                  AND (claim_expires_at IS NULL OR claim_expires_at <= NOW())
                ORDER BY created_at ASC
                LIMIT $1
                FOR UPDATE SKIP LOCKED
            )
            UPDATE application_outbox AS outbox
            SET
                claim_token = $2,
                claimed_at = NOW(),
                claim_expires_at = NOW() + ($3 * INTERVAL '1 second'),
                publish_attempts = outbox.publish_attempts + 1,
                updated_at = NOW()
            FROM next_events
            WHERE outbox.event_id = next_events.event_id
            RETURNING outbox.event_id, outbox.payload, outbox.claim_token, outbox.publish_attempts
            """
        case "feature_outbox":
            return """
            WITH next_events AS (
                SELECT event_id
                FROM feature_outbox
                WHERE published_at IS NULL
                  AND dead_lettered_at IS NULL
                  AND (claim_expires_at IS NULL OR claim_expires_at <= NOW())
                ORDER BY created_at ASC
                LIMIT $1
                FOR UPDATE SKIP LOCKED
            )
            UPDATE feature_outbox AS outbox
            SET
                claim_token = $2,
                claimed_at = NOW(),
                claim_expires_at = NOW() + ($3 * INTERVAL '1 second'),
                publish_attempts = outbox.publish_attempts + 1,
                updated_at = NOW()
            FROM next_events
            WHERE outbox.event_id = next_events.event_id
            RETURNING outbox.event_id, outbox.payload, outbox.claim_token, outbox.publish_attempts
            """
        case "scoring_outbox":
            return """
            WITH next_events AS (
                SELECT event_id
                FROM scoring_outbox
                WHERE published_at IS NULL
                  AND dead_lettered_at IS NULL
                  AND (claim_expires_at IS NULL OR claim_expires_at <= NOW())
                ORDER BY created_at ASC
                LIMIT $1
                FOR UPDATE SKIP LOCKED
            )
            UPDATE scoring_outbox AS outbox
            SET
                claim_token = $2,
                claimed_at = NOW(),
                claim_expires_at = NOW() + ($3 * INTERVAL '1 second'),
                publish_attempts = outbox.publish_attempts + 1,
                updated_at = NOW()
            FROM next_events
            WHERE outbox.event_id = next_events.event_id
            RETURNING outbox.event_id, outbox.payload, outbox.claim_token, outbox.publish_attempts
            """
        case "decision_outbox":
            return """
            WITH next_events AS (
                SELECT event_id
                FROM decision_outbox
                WHERE published_at IS NULL
                  AND dead_lettered_at IS NULL
                  AND (claim_expires_at IS NULL OR claim_expires_at <= NOW())
                ORDER BY created_at ASC
                LIMIT $1
                FOR UPDATE SKIP LOCKED
            )
            UPDATE decision_outbox AS outbox
            SET
                claim_token = $2,
                claimed_at = NOW(),
                claim_expires_at = NOW() + ($3 * INTERVAL '1 second'),
                publish_attempts = outbox.publish_attempts + 1,
                updated_at = NOW()
            FROM next_events
            WHERE outbox.event_id = next_events.event_id
            RETURNING outbox.event_id, outbox.payload, outbox.claim_token, outbox.publish_attempts
            """
        case "assistant_outbox":
            return """
            WITH next_events AS (
                SELECT event_id
                FROM assistant_outbox
                WHERE published_at IS NULL
                  AND dead_lettered_at IS NULL
                  AND (claim_expires_at IS NULL OR claim_expires_at <= NOW())
                ORDER BY created_at ASC
                LIMIT $1
                FOR UPDATE SKIP LOCKED
            )
            UPDATE assistant_outbox AS outbox
            SET
                claim_token = $2,
                claimed_at = NOW(),
                claim_expires_at = NOW() + ($3 * INTERVAL '1 second'),
                publish_attempts = outbox.publish_attempts + 1,
                updated_at = NOW()
            FROM next_events
            WHERE outbox.event_id = next_events.event_id
            RETURNING outbox.event_id, outbox.payload, outbox.claim_token, outbox.publish_attempts
            """
        case "mlops_outbox":
            return """
            WITH next_events AS (
                SELECT event_id
                FROM mlops_outbox
                WHERE published_at IS NULL
                  AND dead_lettered_at IS NULL
                  AND (claim_expires_at IS NULL OR claim_expires_at <= NOW())
                ORDER BY created_at ASC
                LIMIT $1
                FOR UPDATE SKIP LOCKED
            )
            UPDATE mlops_outbox AS outbox
            SET
                claim_token = $2,
                claimed_at = NOW(),
                claim_expires_at = NOW() + ($3 * INTERVAL '1 second'),
                publish_attempts = outbox.publish_attempts + 1,
                updated_at = NOW()
            FROM next_events
            WHERE outbox.event_id = next_events.event_id
            RETURNING outbox.event_id, outbox.payload, outbox.claim_token, outbox.publish_attempts
            """
        case _:
            return _unsupported_validated_table(table_name)


def _outbox_mark_published_query(table_name: str) -> str:
    match table_name:
        case "application_outbox":
            return """
            UPDATE application_outbox
            SET
                published_at = $3,
                claim_token = NULL,
                claimed_at = NULL,
                claim_expires_at = NULL,
                last_error = NULL,
                updated_at = NOW()
            WHERE event_id = $1
              AND claim_token = $2
              AND published_at IS NULL
            """
        case "feature_outbox":
            return """
            UPDATE feature_outbox
            SET
                published_at = $3,
                claim_token = NULL,
                claimed_at = NULL,
                claim_expires_at = NULL,
                last_error = NULL,
                updated_at = NOW()
            WHERE event_id = $1
              AND claim_token = $2
              AND published_at IS NULL
            """
        case "scoring_outbox":
            return """
            UPDATE scoring_outbox
            SET
                published_at = $3,
                claim_token = NULL,
                claimed_at = NULL,
                claim_expires_at = NULL,
                last_error = NULL,
                updated_at = NOW()
            WHERE event_id = $1
              AND claim_token = $2
              AND published_at IS NULL
            """
        case "decision_outbox":
            return """
            UPDATE decision_outbox
            SET
                published_at = $3,
                claim_token = NULL,
                claimed_at = NULL,
                claim_expires_at = NULL,
                last_error = NULL,
                updated_at = NOW()
            WHERE event_id = $1
              AND claim_token = $2
              AND published_at IS NULL
            """
        case "assistant_outbox":
            return """
            UPDATE assistant_outbox
            SET
                published_at = $3,
                claim_token = NULL,
                claimed_at = NULL,
                claim_expires_at = NULL,
                last_error = NULL,
                updated_at = NOW()
            WHERE event_id = $1
              AND claim_token = $2
              AND published_at IS NULL
            """
        case "mlops_outbox":
            return """
            UPDATE mlops_outbox
            SET
                published_at = $3,
                claim_token = NULL,
                claimed_at = NULL,
                claim_expires_at = NULL,
                last_error = NULL,
                updated_at = NOW()
            WHERE event_id = $1
              AND claim_token = $2
              AND published_at IS NULL
            """
        case _:
            return _unsupported_validated_table(table_name)


def _outbox_mark_failed_query(table_name: str) -> str:
    match table_name:
        case "application_outbox":
            return """
            UPDATE application_outbox
            SET
                last_error = $3,
                dead_lettered_at = CASE
                    WHEN publish_attempts >= $4 THEN NOW()
                    ELSE dead_lettered_at
                END,
                claim_token = NULL,
                claimed_at = NULL,
                claim_expires_at = NULL,
                updated_at = NOW()
            WHERE event_id = $1
              AND claim_token = $2
              AND published_at IS NULL
            """
        case "feature_outbox":
            return """
            UPDATE feature_outbox
            SET
                last_error = $3,
                dead_lettered_at = CASE
                    WHEN publish_attempts >= $4 THEN NOW()
                    ELSE dead_lettered_at
                END,
                claim_token = NULL,
                claimed_at = NULL,
                claim_expires_at = NULL,
                updated_at = NOW()
            WHERE event_id = $1
              AND claim_token = $2
              AND published_at IS NULL
            """
        case "scoring_outbox":
            return """
            UPDATE scoring_outbox
            SET
                last_error = $3,
                dead_lettered_at = CASE
                    WHEN publish_attempts >= $4 THEN NOW()
                    ELSE dead_lettered_at
                END,
                claim_token = NULL,
                claimed_at = NULL,
                claim_expires_at = NULL,
                updated_at = NOW()
            WHERE event_id = $1
              AND claim_token = $2
              AND published_at IS NULL
            """
        case "decision_outbox":
            return """
            UPDATE decision_outbox
            SET
                last_error = $3,
                dead_lettered_at = CASE
                    WHEN publish_attempts >= $4 THEN NOW()
                    ELSE dead_lettered_at
                END,
                claim_token = NULL,
                claimed_at = NULL,
                claim_expires_at = NULL,
                updated_at = NOW()
            WHERE event_id = $1
              AND claim_token = $2
              AND published_at IS NULL
            """
        case "assistant_outbox":
            return """
            UPDATE assistant_outbox
            SET
                last_error = $3,
                dead_lettered_at = CASE
                    WHEN publish_attempts >= $4 THEN NOW()
                    ELSE dead_lettered_at
                END,
                claim_token = NULL,
                claimed_at = NULL,
                claim_expires_at = NULL,
                updated_at = NOW()
            WHERE event_id = $1
              AND claim_token = $2
              AND published_at IS NULL
            """
        case "mlops_outbox":
            return """
            UPDATE mlops_outbox
            SET
                last_error = $3,
                dead_lettered_at = CASE
                    WHEN publish_attempts >= $4 THEN NOW()
                    ELSE dead_lettered_at
                END,
                claim_token = NULL,
                claimed_at = NULL,
                claim_expires_at = NULL,
                updated_at = NOW()
            WHERE event_id = $1
              AND claim_token = $2
              AND published_at IS NULL
            """
        case _:
            return _unsupported_validated_table(table_name)


def _inbox_insert_query(table_name: str) -> str:
    match table_name:
        case "application_inbox":
            return """
            INSERT INTO application_inbox
            (event_id, event_name, trace_id, payload, received_at)
            VALUES ($1, $2, $3, $4::jsonb, NOW())
            ON CONFLICT (event_id) DO NOTHING
            RETURNING event_id
            """
        case "feature_inbox":
            return """
            INSERT INTO feature_inbox
            (event_id, event_name, trace_id, payload, received_at)
            VALUES ($1, $2, $3, $4::jsonb, NOW())
            ON CONFLICT (event_id) DO NOTHING
            RETURNING event_id
            """
        case "scoring_inbox":
            return """
            INSERT INTO scoring_inbox
            (event_id, event_name, trace_id, payload, received_at)
            VALUES ($1, $2, $3, $4::jsonb, NOW())
            ON CONFLICT (event_id) DO NOTHING
            RETURNING event_id
            """
        case "decision_inbox":
            return """
            INSERT INTO decision_inbox
            (event_id, event_name, trace_id, payload, received_at)
            VALUES ($1, $2, $3, $4::jsonb, NOW())
            ON CONFLICT (event_id) DO NOTHING
            RETURNING event_id
            """
        case "assistant_inbox":
            return """
            INSERT INTO assistant_inbox
            (event_id, event_name, trace_id, payload, received_at)
            VALUES ($1, $2, $3, $4::jsonb, NOW())
            ON CONFLICT (event_id) DO NOTHING
            RETURNING event_id
            """
        case "audit_inbox":
            return """
            INSERT INTO audit_inbox
            (event_id, event_name, trace_id, payload, received_at)
            VALUES ($1, $2, $3, $4::jsonb, NOW())
            ON CONFLICT (event_id) DO NOTHING
            RETURNING event_id
            """
        case _:
            return _unsupported_validated_table(table_name)


def _unsupported_validated_table(table_name: str) -> Never:
    raise AssertionError(f"Unsupported outbox/inbox table after validation: {table_name}")


async def enqueue_outbox_event(db: DatabaseExecutor, table_name: str, event: EventEnvelope) -> None:
    """Persist event in outbox table before publish."""
    table = _resolve_table(
        table_name,
        operation="enqueue_outbox_event",
        table_set=_OUTBOX_TABLES,
    )
    await db.execute(
        _outbox_insert_query(table),
        event.event_id,
        event.event_name,
        json.dumps(event.model_dump(mode="json")),
    )


async def fetch_pending_outbox_events(
    db: DatabaseExecutor,
    table_name: str,
    limit: int = 100,
    *,
    lease_seconds: int = 30,
) -> list[ClaimedOutboxEvent]:
    """Claim unpublished outbox events in FIFO order for a bounded lease window."""
    operation = "fetch_pending_outbox_events"
    table = _resolve_table(table_name, operation=operation, table_set=_OUTBOX_TABLES)
    claim_token = str(uuid4())
    records = await db.fetch(
        _outbox_claim_query(table),
        limit,
        claim_token,
        lease_seconds,
    )
    events: list[ClaimedOutboxEvent] = []
    for record in records:
        payload = _normalize_payload(
            record["payload"],
            table_name=table_name,
            operation=operation,
        )
        try:
            envelope = EventEnvelope.model_validate(payload)
        except ValidationError as exc:
            raise ServiceError(
                error_code="OUTBOX_PAYLOAD_SCHEMA_INVALID",
                message="Outbox payload failed event envelope validation",
                operation=operation,
                status_code=500,
                cause=str(exc),
                hint=f"Validate outbox payload shape for table '{table_name}'",
            ) from exc
        raw_claim_token = record["claim_token"]
        raw_attempts = record["publish_attempts"]
        if not isinstance(raw_claim_token, str) or raw_claim_token.strip() == "":
            raise ServiceError(
                error_code="OUTBOX_CLAIM_INVALID",
                message="Claimed outbox row is missing claim token",
                operation=operation,
                status_code=500,
                cause=table_name,
            )
        if not isinstance(raw_attempts, int):
            raise ServiceError(
                error_code="OUTBOX_CLAIM_INVALID",
                message="Claimed outbox row is missing publish attempts",
                operation=operation,
                status_code=500,
                cause=table_name,
            )
        events.append(
            ClaimedOutboxEvent(
                event=envelope,
                claim_token=raw_claim_token,
                publish_attempts=raw_attempts,
            )
        )
    return events


async def mark_outbox_event_published(
    db: DatabaseExecutor,
    table_name: str,
    event_id: str,
    *,
    claim_token: str,
) -> None:
    """Mark outbox event as published if the caller still holds the claim."""
    table = _resolve_table(
        table_name,
        operation="mark_outbox_event_published",
        table_set=_OUTBOX_TABLES,
    )
    update_result = await db.execute(
        _outbox_mark_published_query(table),
        event_id,
        claim_token,
        datetime.now(UTC),
    )
    if update_result != _UPDATE_SINGLE_ROW_TAG:
        raise ServiceError(
            error_code="OUTBOX_CLAIM_LOST",
            message="Outbox event claim was lost before publish completion",
            operation="mark_outbox_event_published",
            status_code=409,
            cause=f"{table_name}:{event_id}",
        )


async def mark_outbox_event_failed(
    db: DatabaseExecutor,
    table_name: str,
    event_id: str,
    *,
    claim_token: str,
    error_message: str,
    max_attempts: int,
) -> None:
    """Release or dead-letter a claimed outbox event after publish failure."""
    table = _resolve_table(
        table_name,
        operation="mark_outbox_event_failed",
        table_set=_OUTBOX_TABLES,
    )
    update_result = await db.execute(
        _outbox_mark_failed_query(table),
        event_id,
        claim_token,
        error_message,
        max_attempts,
    )
    if update_result != _UPDATE_SINGLE_ROW_TAG:
        raise ServiceError(
            error_code="OUTBOX_CLAIM_LOST",
            message="Outbox event claim was lost before failure handling completed",
            operation="mark_outbox_event_failed",
            status_code=409,
            cause=f"{table_name}:{event_id}",
        )


async def record_inbox_event(db: DatabaseExecutor, table_name: str, event: EventEnvelope) -> bool:
    """Insert event_id into inbox table. Returns False for duplicates."""
    table = _resolve_table(
        table_name,
        operation="record_inbox_event",
        table_set=_INBOX_TABLES,
    )
    inserted = await db.fetchrow(
        _inbox_insert_query(table),
        event.event_id,
        event.event_name,
        event.trace_id,
        json.dumps(event.model_dump(mode="json")),
    )
    return inserted is not None
