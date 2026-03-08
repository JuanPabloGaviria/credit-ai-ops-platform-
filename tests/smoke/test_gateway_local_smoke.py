from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import api_gateway.routes as gateway_routes
import pytest
from api_gateway.idempotency import IdempotencyReservation
from api_gateway.main import app as gateway_app
from fastapi.testclient import TestClient

from contracts import (
    DecisionResult,
    FeatureVector,
    GatewayCreditEvaluationResponse,
    ScorePrediction,
)
from shared_kernel import ServiceError


@dataclass(slots=True)
class _StoredRequest:
    endpoint: str
    request_hash: str
    response_payload: dict[str, Any] | None
    error_payload: dict[str, Any] | None = None
    error_status_code: int | None = None


class _StatefulIdempotencyRepository:
    def __init__(self) -> None:
        self._store: dict[str, _StoredRequest] = {}

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def reserve_request(
        self,
        *,
        idempotency_key: str,
        endpoint: str,
        request_hash: str,
    ) -> IdempotencyReservation:
        stored = self._store.get(idempotency_key)
        if stored is None:
            self._store[idempotency_key] = _StoredRequest(
                endpoint=endpoint,
                request_hash=request_hash,
                response_payload=None,
            )
            return IdempotencyReservation(replay_payload=None)
        if stored.endpoint != endpoint:
            raise ServiceError(
                error_code="IDEMPOTENCY_ENDPOINT_MISMATCH",
                message="Idempotency key already exists for a different endpoint",
                operation="idempotency_reserve",
                status_code=409,
            )
        if stored.request_hash != request_hash:
            raise ServiceError(
                error_code="IDEMPOTENCY_REQUEST_MISMATCH",
                message="Idempotency key already exists for a different request payload",
                operation="idempotency_reserve",
                status_code=409,
            )
        if stored.response_payload is not None:
            return IdempotencyReservation(replay_payload=stored.response_payload)
        raise ServiceError(
            error_code="IDEMPOTENCY_IN_PROGRESS",
            message="Request with this idempotency key is already being processed",
            operation="idempotency_reserve",
            status_code=409,
        )

    async def persist_response(
        self,
        *,
        idempotency_key: str,
        response_payload: dict[str, Any],
        response_status_code: int,
    ) -> None:
        stored = self._store.get(idempotency_key)
        if stored is None:
            raise ServiceError(
                error_code="IDEMPOTENCY_PERSIST_FAILED",
                message="Failed to persist idempotency response payload",
                operation="idempotency_persist",
                status_code=500,
            )
        _ = response_status_code
        stored.response_payload = response_payload

    async def persist_failure(
        self,
        *,
        idempotency_key: str,
        error_payload: dict[str, Any],
        error_status_code: int,
    ) -> None:
        stored = self._store.get(idempotency_key)
        if stored is None:
            raise ServiceError(
                error_code="IDEMPOTENCY_PERSIST_FAILED",
                message="Failed to persist idempotency error payload",
                operation="idempotency_persist_failure",
                status_code=500,
            )
        stored.error_payload = error_payload
        stored.error_status_code = error_status_code


def _install_stateful_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    repository = _StatefulIdempotencyRepository()

    class _RepositoryFactory:
        @classmethod
        def from_dsn(
            cls,
            postgres_dsn: str,
            *,
            ttl_seconds: int | None = None,
            stale_after_seconds: int | None = None,
        ) -> _StatefulIdempotencyRepository:
            _ = cls
            _ = postgres_dsn
            _ = ttl_seconds
            _ = stale_after_seconds
            return repository

    monkeypatch.setattr(gateway_routes, "GatewayIdempotencyRepository", _RepositoryFactory)


def _evaluation_response() -> GatewayCreditEvaluationResponse:
    return GatewayCreditEvaluationResponse(
        features=FeatureVector(
            application_id="app-smoke-000001",
            requested_amount=20000,
            debt_to_income=0.36,
            amount_to_income=0.3333,
            credit_history_months=36,
            existing_defaults=0,
        ),
        score=ScorePrediction(
            application_id="app-smoke-000001",
            requested_amount=20000,
            risk_score=0.31,
            model_version="baseline_lr_v1",
            reason_codes=["LOW_RISK_PROFILE"],
        ),
        decision=DecisionResult(
            application_id="app-smoke-000001",
            risk_score=0.31,
            decision="approve",
            reason_codes=["LOW_RISK_PROFILE", "POLICY_AUTO_APPROVE"],
        ),
    )


def _install_fake_evaluation(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_build_credit_evaluation(
        application: Any,
        *,
        settings: Any,
    ) -> GatewayCreditEvaluationResponse:
        _ = application
        _ = settings
        return _evaluation_response()

    monkeypatch.setattr(gateway_routes, "_build_credit_evaluation", _fake_build_credit_evaluation)


def _application_payload(requested_amount: float = 20000.0) -> dict[str, Any]:
    return {
        "application_id": "app-smoke-000001",
        "applicant_id": "applicant-smoke-000001",
        "monthly_income": 5000.0,
        "monthly_debt": 1800.0,
        "requested_amount": requested_amount,
        "credit_history_months": 36,
        "existing_defaults": 0,
    }


@pytest.mark.smoke
def test_gateway_local_smoke_health_and_idempotency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_stateful_repository(monkeypatch)
    _install_fake_evaluation(monkeypatch)
    client = TestClient(gateway_app)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    first = client.post(
        "/v1/gateway/credit-evaluate",
        json=_application_payload(),
        headers={"x-idempotency-key": "idem-smoke-00000001"},
    )
    assert first.status_code == 200
    first_payload = first.json()

    replay = client.post(
        "/v1/gateway/credit-evaluate",
        json=_application_payload(),
        headers={"x-idempotency-key": "idem-smoke-00000001"},
    )
    assert replay.status_code == 200
    assert replay.json() == first_payload

    conflict = client.post(
        "/v1/gateway/credit-evaluate",
        json=_application_payload(requested_amount=25000.0),
        headers={"x-idempotency-key": "idem-smoke-00000001"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["error_code"] == "IDEMPOTENCY_REQUEST_MISMATCH"
