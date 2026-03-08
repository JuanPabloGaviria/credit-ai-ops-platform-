"""collab-assistant API routes."""

from __future__ import annotations

from fastapi import APIRouter, Header, Path

from contracts import AssistantSummaryRequest, AssistantSummaryResponse
from shared_kernel import (
    authorize_request,
    get_trace_id,
    load_settings,
    normalize_optional_idempotency_key,
)

from .repositories import AssistantRepository

router = APIRouter(prefix="/v1", tags=["collab-assistant"])


@router.get("/assistant/status")
async def service_status(
    x_idempotency_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    _ = normalize_optional_idempotency_key(
        x_idempotency_key,
        operation="assistant_status",
    )
    settings = load_settings("collab-assistant")
    await authorize_request(
        settings=settings,
        authorization=authorization,
        operation="assistant_status",
    )
    return {"service": "collab-assistant", "status": "operational"}


@router.post("/assistant/summarize")
async def summarize(
    request: AssistantSummaryRequest,
    x_idempotency_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> AssistantSummaryResponse:
    _ = normalize_optional_idempotency_key(
        x_idempotency_key,
        operation="assistant_summarize",
    )
    settings = load_settings("collab-assistant")
    await authorize_request(
        settings=settings,
        authorization=authorization,
        operation="assistant_summarize",
    )
    repository = AssistantRepository(settings)
    await repository.connect()
    try:
        response = await repository.summarize_request(request=request, trace_id=get_trace_id())
        return response
    finally:
        await repository.close()


@router.get("/assistant/summaries/{application_id}")
async def get_summary(
    application_id: str = Path(min_length=8),
    x_idempotency_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> AssistantSummaryResponse:
    _ = normalize_optional_idempotency_key(
        x_idempotency_key,
        operation="assistant_get_summary",
    )
    settings = load_settings("collab-assistant")
    await authorize_request(
        settings=settings,
        authorization=authorization,
        operation="assistant_get_summary",
    )
    repository = AssistantRepository(settings)
    await repository.connect()
    try:
        return await repository.get_summary(application_id)
    finally:
        await repository.close()
