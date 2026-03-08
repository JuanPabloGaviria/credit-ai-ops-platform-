"""Typed downstream service clients used by api-gateway orchestration."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, TypeVar, cast

import httpx
from opentelemetry.trace import SpanKind, Status, StatusCode
from pydantic import BaseModel, ValidationError

from contracts import DecisionRequest, DecisionResult, FeatureVector, ScorePrediction
from observability import MetricsRegistry
from shared_kernel import (
    ServiceError,
    ServiceSettings,
    build_service_authorization,
    correlation_id_for,
)
from shared_kernel.resilience import with_retries, with_timeout
from shared_kernel.telemetry import get_tracer, inject_trace_context
from shared_kernel.tracing import (
    CAUSATION_ID_HEADER,
    CORRELATION_ID_HEADER,
    TRACE_ID_HEADER,
    get_causation_id,
)

T = TypeVar("T", bound=BaseModel)


class GatewayPipelineClient:
    """Sequential gateway orchestration against real downstream services."""

    def __init__(self, settings: ServiceSettings) -> None:
        self._settings = settings
        self._metrics = MetricsRegistry(settings.service_name)
        self._tracer = get_tracer("api_gateway.clients")

    async def evaluate_credit(
        self,
        *,
        application_payload: Mapping[str, Any],
        trace_id: str,
    ) -> tuple[FeatureVector, ScorePrediction, DecisionResult]:
        authorization = await build_service_authorization(self._settings)
        common_headers = {TRACE_ID_HEADER: trace_id}
        common_headers[CORRELATION_ID_HEADER] = correlation_id_for(trace_id)
        causation_id = get_causation_id()
        if causation_id is not None:
            common_headers[CAUSATION_ID_HEADER] = causation_id
        if authorization is not None:
            common_headers["Authorization"] = authorization

        features = await self._post_json(
            base_url=self._settings.feature_service_url,
            dependency="feature-service",
            path="/v1/features/materialize",
            payload=application_payload,
            response_model=FeatureVector,
            operation="gateway_feature_materialize",
            headers=common_headers,
        )
        score = await self._post_json(
            base_url=self._settings.scoring_service_url,
            dependency="scoring-service",
            path="/v1/scores/predict",
            payload=features.model_dump(mode="json"),
            response_model=ScorePrediction,
            operation="gateway_scoring_predict",
            headers=common_headers,
        )
        decision_request = DecisionRequest(
            application_id=score.application_id,
            requested_amount=score.requested_amount,
            risk_score=score.risk_score,
            reason_codes=score.reason_codes,
        )
        decision = await self._post_json(
            base_url=self._settings.decision_service_url,
            dependency="decision-service",
            path="/v1/decisions/evaluate",
            payload=decision_request.model_dump(mode="json"),
            response_model=DecisionResult,
            operation="gateway_decision_evaluate",
            headers=common_headers,
        )
        return features, score, decision

    async def _post_json(
        self,
        *,
        base_url: str,
        dependency: str,
        path: str,
        payload: Mapping[str, Any],
        response_model: type[T],
        operation: str,
        headers: Mapping[str, str],
    ) -> T:
        async def attempt() -> T:
            started = time.perf_counter()
            outcome = "success"
            async with httpx.AsyncClient(
                base_url=base_url,
                timeout=httpx.Timeout(self._settings.request_timeout_seconds),
            ) as client:
                with self._tracer.start_as_current_span(
                    f"HTTP POST {dependency}{path}",
                    kind=SpanKind.CLIENT,
                ) as span:
                    request_headers = dict(headers)
                    inject_trace_context(request_headers)
                    span.set_attribute("http.method", "POST")
                    span.set_attribute("http.route", path)
                    span.set_attribute("http.url", f"{base_url.rstrip('/')}{path}")
                    span.set_attribute("server.address", dependency)
                    try:
                        response = await with_timeout(
                            client.post(path, json=dict(payload), headers=request_headers),
                            timeout_seconds=self._settings.request_timeout_seconds,
                            operation=operation,
                        )
                        span.set_attribute("http.status_code", response.status_code)
                        if response.status_code >= 400:
                            span.set_status(Status(StatusCode.ERROR))
                        decoded = _decode_response(
                            response=response,
                            response_model=response_model,
                            operation=operation,
                        )
                    except ServiceError as exc:
                        outcome = "rejected" if 400 <= exc.status_code < 500 else "failed"
                        span.record_exception(exc)
                        span.set_status(Status(StatusCode.ERROR))
                        raise
                    except Exception as exc:
                        outcome = "failed"
                        span.record_exception(exc)
                        span.set_status(Status(StatusCode.ERROR))
                        raise
                    else:
                        return decoded
                    finally:
                        self._metrics.observe_integration_call(
                            dependency=dependency,
                            operation=operation,
                            outcome=outcome,
                            latency_seconds=time.perf_counter() - started,
                        )

        return await with_retries(
            operation=operation,
            attemptable=attempt,
            max_attempts=self._settings.retry_max_attempts,
            base_delay_seconds=self._settings.retry_base_delay_seconds,
            max_delay_seconds=self._settings.retry_max_delay_seconds,
            jitter_seconds=self._settings.retry_jitter_seconds,
        )


def _decode_response(
    *,
    response: httpx.Response,
    response_model: type[T],
    operation: str,
) -> T:
    if response.status_code >= 500:
        raise ServiceError(
            error_code="DOWNSTREAM_SERVICE_UNAVAILABLE",
            message="Downstream service returned a server error",
            operation=operation,
            status_code=503,
            cause=f"http_status={response.status_code}",
        )

    if response.status_code >= 400:
        payload = _decode_json(response=response, operation=operation)
        error_code = _mapping_string(payload, key="error_code") or "DOWNSTREAM_REQUEST_FAILED"
        message = _mapping_string(payload, key="message") or "Downstream service rejected request"
        raise ServiceError(
            error_code=error_code,
            message=message,
            operation=operation,
            status_code=response.status_code,
            cause=f"http_status={response.status_code}",
        )

    payload = _decode_json(response=response, operation=operation)
    try:
        return response_model.model_validate(payload)
    except ValidationError as exc:
        raise ServiceError(
            error_code="DOWNSTREAM_RESPONSE_INVALID",
            message="Downstream service returned an invalid response payload",
            operation=operation,
            status_code=502,
            cause=str(exc),
        ) from exc


def _decode_json(*, response: httpx.Response, operation: str) -> Mapping[str, Any]:
    try:
        raw_payload = response.json()
    except ValueError as exc:
        raise ServiceError(
            error_code="DOWNSTREAM_RESPONSE_INVALID",
            message="Downstream service returned non-JSON payload",
            operation=operation,
            status_code=502,
            cause=str(exc),
        ) from exc
    if not isinstance(raw_payload, Mapping):
        raise ServiceError(
            error_code="DOWNSTREAM_RESPONSE_INVALID",
            message="Downstream service returned malformed JSON payload",
            operation=operation,
            status_code=502,
            cause=type(raw_payload).__name__,
        )
    return cast(Mapping[str, Any], raw_payload)


def _mapping_string(payload: Mapping[str, Any], *, key: str) -> str | None:
    value = payload.get(key)
    if isinstance(value, str) and value.strip() != "":
        return value
    return None
