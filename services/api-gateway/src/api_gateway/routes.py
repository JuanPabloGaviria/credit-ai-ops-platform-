"""api-gateway API routes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse

from contracts import (
    ApplicationInput,
    DecisionResult,
    FeatureVector,
    GatewayCreditEvaluationResponse,
    ScorePrediction,
)
from shared_kernel import (
    ServiceError,
    ServiceSettings,
    get_trace_id,
    load_settings,
    normalize_optional_idempotency_key,
    require_idempotency_key,
)

from .auth import authorize_gateway_request
from .clients import GatewayPipelineClient
from .idempotency import GatewayIdempotencyRepository, compute_request_hash

router = APIRouter(prefix="/v1", tags=["api-gateway"])
_CREDIT_EVALUATE_ENDPOINT = "/v1/gateway/credit-evaluate"
GatewayResponse = GatewayCreditEvaluationResponse


def _validate_idempotency_key(idempotency_key: str | None, operation: str) -> None:
    _ = normalize_optional_idempotency_key(
        idempotency_key,
        operation=operation,
    )


def _build_gateway_pipeline(settings: ServiceSettings) -> GatewayPipelineClient:
    return GatewayPipelineClient(settings)


async def _build_credit_evaluation(
    application: ApplicationInput,
    *,
    settings: ServiceSettings,
) -> GatewayResponse:
    trace_id = get_trace_id()
    pipeline = _build_gateway_pipeline(settings)
    features, score, decision = await pipeline.evaluate_credit(
        application_payload=application.model_dump(mode="json"),
        trace_id=trace_id,
    )
    return GatewayCreditEvaluationResponse(
        features=features,
        score=score,
        decision=decision,
    )


def _serialize_credit_evaluation(payload: GatewayResponse) -> dict[str, dict[str, Any]]:
    return {
        "features": payload.features.model_dump(mode="json"),
        "score": payload.score.model_dump(mode="json"),
        "decision": payload.decision.model_dump(mode="json"),
    }


def _deserialize_credit_evaluation(payload: Mapping[str, Any]) -> GatewayResponse:
    try:
        features = FeatureVector.model_validate(payload["features"])
        score = ScorePrediction.model_validate(payload["score"])
        decision = DecisionResult.model_validate(payload["decision"])
    except Exception as exc:
        raise ServiceError(
            error_code="IDEMPOTENCY_PAYLOAD_INVALID",
            message="Stored idempotency payload is invalid",
            operation="credit_evaluate_deserialize",
            status_code=500,
            cause=str(exc),
        ) from exc
    return GatewayCreditEvaluationResponse(features=features, score=score, decision=decision)


@router.get("/gateway/status")
async def service_status(
    x_idempotency_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    _validate_idempotency_key(x_idempotency_key, "gateway_status")
    settings = load_settings("api-gateway")
    await authorize_gateway_request(
        settings=settings,
        authorization=authorization,
        operation="gateway_status",
    )
    return {"service": "api-gateway", "status": "operational"}


@router.post(
    "/gateway/credit-evaluate",
    response_model=GatewayCreditEvaluationResponse,
)
async def credit_evaluate(
    application: ApplicationInput,
    x_idempotency_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> GatewayCreditEvaluationResponse | JSONResponse:
    idempotency_key = require_idempotency_key(
        x_idempotency_key,
        operation="credit_evaluate",
        missing_message="Idempotency key is required for credit evaluation requests",
        missing_hint="Pass x-idempotency-key header with a stable unique request key",
    )
    settings = load_settings("api-gateway")
    await authorize_gateway_request(
        settings=settings,
        authorization=authorization,
        operation="credit_evaluate",
    )
    repository = GatewayIdempotencyRepository.from_dsn(
        settings.postgres_dsn,
        ttl_seconds=settings.idempotency_ttl_seconds,
        stale_after_seconds=settings.idempotency_stale_after_seconds,
    )

    await repository.connect()
    try:
        request_payload = application.model_dump(mode="json")
        request_hash = compute_request_hash(request_payload)
        reservation = await repository.reserve_request(
            idempotency_key=idempotency_key,
            endpoint=_CREDIT_EVALUATE_ENDPOINT,
            request_hash=request_hash,
        )
        if reservation.replay_payload is not None:
            return _deserialize_credit_evaluation(reservation.replay_payload)
        if reservation.replay_error is not None:
            replay_status = reservation.replay_status_code or 500
            return JSONResponse(status_code=replay_status, content=reservation.replay_error)

        try:
            evaluation = await _build_credit_evaluation(application, settings=settings)
        except ServiceError as exc:
            error_payload = exc.to_envelope(
                service=settings.service_name,
                trace_id=get_trace_id(),
            ).model_dump(mode="json")
            await repository.persist_failure(
                idempotency_key=idempotency_key,
                error_payload=error_payload,
                error_status_code=exc.status_code,
            )
            raise
        except Exception:
            unexpected = ServiceError(
                error_code="UNEXPECTED_ERROR",
                message="Unexpected runtime failure",
                operation="credit_evaluate",
                status_code=500,
                hint=(
                    "Inspect structured logs with trace ID to identify the failing downstream edge"
                ),
            )
            error_payload = unexpected.to_envelope(
                service=settings.service_name,
                trace_id=get_trace_id(),
            ).model_dump(mode="json")
            await repository.persist_failure(
                idempotency_key=idempotency_key,
                error_payload=error_payload,
                error_status_code=unexpected.status_code,
            )
            raise
        await repository.persist_response(
            idempotency_key=idempotency_key,
            response_payload=_serialize_credit_evaluation(evaluation),
            response_status_code=200,
        )
        return evaluation
    finally:
        await repository.close()
