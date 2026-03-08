"""observability-audit service API routes."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Header, Path, Query
from pydantic import BaseModel, ConfigDict

from contracts import EventEnvelope
from security import redact_pii
from shared_kernel import (
    ServiceError,
    authorize_request,
    correlation_id_for,
    get_causation_id,
    get_trace_id,
    load_settings,
    normalize_optional_idempotency_key,
)

from .repositories import AuditEventRecord, AuditRepository

router = APIRouter(prefix="/v1", tags=["observability-audit"])
_EVENT_NAME_PATTERN = re.compile(r"^[a-z]+\.[a-z_]+\.[a-z_]+\.v\d+$")


class AuditEvent(BaseModel):
    """Audit event payload with PII-safe redaction."""

    model_config = ConfigDict(extra="forbid")

    event_name: str
    payload: dict[str, Any]


class AuditEventResponse(BaseModel):
    """Typed API representation of persisted audit event."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    event_name: str
    payload: dict[str, Any]
    trace_id: str | None
    correlation_id: str
    causation_id: str | None
    created_at: datetime


class AuditEventListResponse(BaseModel):
    """List response for audit event queries."""

    model_config = ConfigDict(extra="forbid")

    events: list[AuditEventResponse]
    count: int


class AuditWriteResponse(BaseModel):
    """Write acknowledgment payload."""

    model_config = ConfigDict(extra="forbid")

    status: str
    event_name: str
    payload: dict[str, Any]
    event_id: str


class _AuditQueryFilters(BaseModel):
    """Validated query filters for audit read endpoints."""

    model_config = ConfigDict(extra="forbid")

    event_name: str | None = None
    trace_id: str | None = None
    correlation_id: str | None = None
    limit: int


def _validate_event_name(event_name: str, operation: str) -> None:
    if _EVENT_NAME_PATTERN.fullmatch(event_name) is not None:
        return
    raise ServiceError(
        error_code="INVALID_EVENT_NAME",
        message="Event name must follow <domain>.<entity>.<action>.vN format",
        operation=operation,
        status_code=400,
        cause=event_name,
        hint="Example: credit.decision.made.v1",
    )


def _to_response(record: AuditEventRecord) -> AuditEventResponse:
    return AuditEventResponse(
        event_id=record.event_id,
        event_name=record.event_name,
        payload=record.payload,
        trace_id=record.trace_id,
        correlation_id=record.correlation_id,
        causation_id=record.causation_id,
        created_at=record.created_at,
    )


@router.get("/audit/status")
async def service_status(
    x_idempotency_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    _ = normalize_optional_idempotency_key(
        x_idempotency_key,
        operation="audit_status",
    )
    settings = load_settings("observability-audit")
    await authorize_request(
        settings=settings,
        authorization=authorization,
        operation="audit_status",
    )
    return {"service": "observability-audit", "status": "operational"}


@router.post("/audit/events")
async def write_event(
    event: AuditEvent,
    x_idempotency_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> AuditWriteResponse:
    _ = normalize_optional_idempotency_key(
        x_idempotency_key,
        operation="audit_write_event",
    )
    settings = load_settings("observability-audit")
    await authorize_request(
        settings=settings,
        authorization=authorization,
        operation="audit_write_event",
    )
    _validate_event_name(event.event_name, "audit_write_event")
    redacted_payload = redact_pii(event.payload)
    trace_id = get_trace_id()
    envelope = EventEnvelope(
        event_name=event.event_name,
        event_id=str(uuid4()),
        trace_id=trace_id,
        correlation_id=correlation_id_for(trace_id),
        causation_id=get_causation_id(),
        producer="observability-audit-service",
        payload=redacted_payload,
    )

    repository = AuditRepository(settings.postgres_dsn)
    await repository.connect()
    try:
        await repository.handle_event(envelope)
    finally:
        await repository.close()

    return AuditWriteResponse(
        status="accepted",
        event_name=event.event_name,
        payload=redacted_payload,
        event_id=envelope.event_id,
    )


@router.get("/audit/events")
async def list_events(
    event_name: str | None = Query(default=None),
    trace_id: str | None = Query(default=None, min_length=8),
    correlation_id: str | None = Query(default=None, min_length=8),
    limit: int = Query(default=50, ge=1, le=200),
    x_idempotency_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> AuditEventListResponse:
    _ = normalize_optional_idempotency_key(
        x_idempotency_key,
        operation="audit_list_events",
    )
    settings = load_settings("observability-audit")
    await authorize_request(
        settings=settings,
        authorization=authorization,
        operation="audit_list_events",
    )
    filters = _AuditQueryFilters(
        event_name=event_name,
        trace_id=trace_id,
        correlation_id=correlation_id,
        limit=limit,
    )
    if filters.event_name is not None:
        _validate_event_name(filters.event_name, "audit_list_events")

    repository = AuditRepository(settings.postgres_dsn)
    await repository.connect()
    try:
        events = await repository.list_events(
            event_name=filters.event_name,
            trace_id=filters.trace_id,
            correlation_id=filters.correlation_id,
            limit=filters.limit,
        )
    finally:
        await repository.close()

    responses = [_to_response(event) for event in events]
    return AuditEventListResponse(events=responses, count=len(responses))


@router.get("/audit/events/{event_id}")
async def get_event(
    event_id: str = Path(min_length=8),
    x_idempotency_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> AuditEventResponse:
    _ = normalize_optional_idempotency_key(
        x_idempotency_key,
        operation="audit_get_event",
    )
    settings = load_settings("observability-audit")
    await authorize_request(
        settings=settings,
        authorization=authorization,
        operation="audit_get_event",
    )
    repository = AuditRepository(settings.postgres_dsn)
    await repository.connect()
    try:
        event = await repository.get_event(event_id)
    finally:
        await repository.close()

    if event is None:
        raise ServiceError(
            error_code="AUDIT_EVENT_NOT_FOUND",
            message="Audit event was not found",
            operation="audit_get_event",
            status_code=404,
            cause=event_id,
        )

    return _to_response(event)


@router.get("/audit/traces/{trace_id}")
async def list_trace_events(
    trace_id: str = Path(min_length=8),
    limit: int = Query(default=200, ge=1, le=500),
    x_idempotency_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> AuditEventListResponse:
    _ = normalize_optional_idempotency_key(
        x_idempotency_key,
        operation="audit_list_trace_events",
    )
    settings = load_settings("observability-audit")
    await authorize_request(
        settings=settings,
        authorization=authorization,
        operation="audit_list_trace_events",
    )
    repository = AuditRepository(settings.postgres_dsn)
    await repository.connect()
    try:
        events = await repository.list_events(
            event_name=None,
            trace_id=trace_id,
            correlation_id=None,
            limit=limit,
        )
    finally:
        await repository.close()

    responses = [_to_response(event) for event in events]
    return AuditEventListResponse(events=responses, count=len(responses))
