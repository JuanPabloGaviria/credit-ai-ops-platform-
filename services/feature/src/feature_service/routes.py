"""feature service API routes."""

from __future__ import annotations

from fastapi import APIRouter, Header

from contracts import ApplicationInput, FeatureVector
from shared_kernel import (
    authorize_request,
    get_trace_id,
    load_settings,
    normalize_optional_idempotency_key,
)

from .repositories import FeatureRepository

router = APIRouter(prefix="/v1", tags=["feature"])


@router.get("/features/status")
async def service_status(
    x_idempotency_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    _ = normalize_optional_idempotency_key(
        x_idempotency_key,
        operation="feature_status",
    )
    settings = load_settings("feature")
    await authorize_request(
        settings=settings,
        authorization=authorization,
        operation="feature_status",
    )
    return {"service": "feature", "status": "operational"}


@router.post("/features/materialize")
async def materialize(
    application: ApplicationInput,
    x_idempotency_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> FeatureVector:
    _ = normalize_optional_idempotency_key(
        x_idempotency_key,
        operation="feature_materialize",
    )
    settings = load_settings("feature")
    await authorize_request(
        settings=settings,
        authorization=authorization,
        operation="feature_materialize",
    )
    repository = FeatureRepository(settings)
    await repository.connect()
    try:
        features = await repository.materialize_from_application(
            application=application,
            trace_id=get_trace_id(),
        )
        return features
    finally:
        await repository.close()
