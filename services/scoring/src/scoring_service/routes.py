"""scoring service API routes."""

from __future__ import annotations

from fastapi import APIRouter, Header

from contracts import FeatureVector, ScorePrediction
from shared_kernel import (
    authorize_request,
    get_trace_id,
    load_settings,
    normalize_optional_idempotency_key,
)

from .repositories import ScoringRepository

router = APIRouter(prefix="/v1", tags=["scoring"])


@router.get("/scores/status")
async def service_status(
    x_idempotency_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    _ = normalize_optional_idempotency_key(
        x_idempotency_key,
        operation="scoring_status",
    )
    settings = load_settings("scoring")
    await authorize_request(
        settings=settings,
        authorization=authorization,
        operation="scoring_status",
    )
    return {"service": "scoring", "status": "operational"}


@router.post("/scores/predict")
async def predict(
    features: FeatureVector,
    x_idempotency_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> ScorePrediction:
    _ = normalize_optional_idempotency_key(
        x_idempotency_key,
        operation="score_predict",
    )
    settings = load_settings("scoring")
    await authorize_request(
        settings=settings,
        authorization=authorization,
        operation="score_predict",
    )
    repository = ScoringRepository(settings)
    await repository.connect()
    try:
        prediction = await repository.score_features(features=features, trace_id=get_trace_id())
        return prediction
    finally:
        await repository.close()
