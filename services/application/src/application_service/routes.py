"""application service API routes."""

from __future__ import annotations

from fastapi import APIRouter, Header

from contracts import ApplicationInput
from shared_kernel import (
    authorize_request,
    get_trace_id,
    load_settings,
    normalize_optional_idempotency_key,
)

from .repositories import ApplicationRepository

router = APIRouter(prefix="/v1", tags=["application"])


@router.get("/applications/status")
async def service_status(
    x_idempotency_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    _ = normalize_optional_idempotency_key(
        x_idempotency_key,
        operation="application_status",
    )
    settings = load_settings("application")
    await authorize_request(
        settings=settings,
        authorization=authorization,
        operation="application_status",
    )
    return {"service": "application", "status": "operational"}


@router.post("/applications/intake")
async def intake_application(
    application: ApplicationInput,
    x_idempotency_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, int | str]:
    _ = normalize_optional_idempotency_key(
        x_idempotency_key,
        operation="application_intake",
    )
    settings = load_settings("application")
    await authorize_request(
        settings=settings,
        authorization=authorization,
        operation="application_intake",
    )
    repository = ApplicationRepository(settings)
    await repository.connect()
    try:
        event_id = await repository.intake_application(
            application=application,
            trace_id=get_trace_id(),
        )
    finally:
        await repository.close()

    return {
        "status": "accepted",
        "application_id": application.application_id,
        "event_id": event_id,
        "queued_events": 1,
    }
