"""decision service API routes."""

from __future__ import annotations

from fastapi import APIRouter, Header

from contracts import DecisionRequest, DecisionResult, ScorePrediction
from shared_kernel import (
    authorize_request,
    get_trace_id,
    load_settings,
    normalize_optional_idempotency_key,
)

from .repositories import DecisionRepository

router = APIRouter(prefix="/v1", tags=["decision"])
_MANUAL_REQUEST_MODEL_VERSION = "manual_request_v1"


@router.get("/decisions/status")
async def service_status(
    x_idempotency_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    _ = normalize_optional_idempotency_key(
        x_idempotency_key,
        operation="decision_status",
    )
    settings = load_settings("decision")
    await authorize_request(
        settings=settings,
        authorization=authorization,
        operation="decision_status",
    )
    return {"service": "decision", "status": "operational"}


@router.post("/decisions/evaluate")
async def evaluate(
    request: DecisionRequest,
    x_idempotency_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> DecisionResult:
    _ = normalize_optional_idempotency_key(
        x_idempotency_key,
        operation="decision_evaluate",
    )
    settings = load_settings("decision")
    await authorize_request(
        settings=settings,
        authorization=authorization,
        operation="decision_evaluate",
    )
    repository = DecisionRepository(settings)
    score = ScorePrediction(
        application_id=request.application_id,
        requested_amount=request.requested_amount,
        risk_score=request.risk_score,
        model_version=_MANUAL_REQUEST_MODEL_VERSION,
        reason_codes=request.reason_codes,
    )
    await repository.connect()
    try:
        decision = await repository.decide_from_score(score=score, trace_id=get_trace_id())
        return decision
    finally:
        await repository.close()
