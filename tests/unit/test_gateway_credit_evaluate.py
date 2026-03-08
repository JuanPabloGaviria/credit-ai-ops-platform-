from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import api_gateway.routes as gateway_routes
import jwt
import pytest
from api_gateway.idempotency import IdempotencyReservation
from api_gateway.main import app
from fastapi.testclient import TestClient

from contracts import (
    DecisionResult,
    FeatureVector,
    GatewayCreditEvaluationResponse,
    ScorePrediction,
)
from shared_kernel import ServiceError, ServiceSettings
from tests.defaults import DEFAULT_POSTGRES_DSN, DEFAULT_RABBITMQ_URL

_AUTH_SHARED_SECRET = "-".join(("test", "shared", "secret", "00000001"))
_AUTH_ISSUER = "https://issuer.example.test"


class _FakeIdempotencyRepository:
    def __init__(
        self,
        *,
        reservation: IdempotencyReservation | None = None,
        reserve_error: ServiceError | None = None,
    ) -> None:
        self._reservation = reservation
        self._reserve_error = reserve_error
        self.reserve_calls: list[tuple[str, str, str]] = []
        self.persist_calls: list[tuple[str, dict[str, Any], int]] = []
        self.persist_failures: list[tuple[str, dict[str, Any], int]] = []

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
        self.reserve_calls.append((idempotency_key, endpoint, request_hash))
        if self._reserve_error is not None:
            raise self._reserve_error
        if self._reservation is None:
            raise AssertionError("reservation must be provided for fake repository")
        return self._reservation

    async def persist_response(
        self,
        *,
        idempotency_key: str,
        response_payload: dict[str, Any],
        response_status_code: int,
    ) -> None:
        self.persist_calls.append((idempotency_key, response_payload, response_status_code))

    async def persist_failure(
        self,
        *,
        idempotency_key: str,
        error_payload: dict[str, Any],
        error_status_code: int,
    ) -> None:
        self.persist_failures.append((idempotency_key, error_payload, error_status_code))


def _install_fake_repository(
    monkeypatch: pytest.MonkeyPatch,
    fake_repository: _FakeIdempotencyRepository,
) -> None:
    class _RepositoryFactory:
        @classmethod
        def from_dsn(
            cls,
            postgres_dsn: str,
            *,
            ttl_seconds: int,
            stale_after_seconds: int,
        ) -> _FakeIdempotencyRepository:
            _ = cls
            _ = postgres_dsn
            _ = ttl_seconds
            _ = stale_after_seconds
            return fake_repository

    monkeypatch.setattr(gateway_routes, "GatewayIdempotencyRepository", _RepositoryFactory)


def _application_payload() -> dict[str, Any]:
    return {
        "application_id": "app-000001",
        "applicant_id": "applicant-001",
        "monthly_income": 5000,
        "monthly_debt": 1800,
        "requested_amount": 20000,
        "credit_history_months": 36,
        "existing_defaults": 0,
    }


def _evaluation_response() -> GatewayCreditEvaluationResponse:
    return GatewayCreditEvaluationResponse(
        features=FeatureVector(
            application_id="app-000001",
            requested_amount=20000,
            debt_to_income=0.36,
            amount_to_income=0.3333,
            credit_history_months=36,
            existing_defaults=0,
        ),
        score=ScorePrediction(
            application_id="app-000001",
            requested_amount=20000,
            risk_score=0.31,
            model_version="baseline_lr_v1",
            reason_codes=["LOW_RISK_PROFILE"],
        ),
        decision=DecisionResult(
            application_id="app-000001",
            risk_score=0.31,
            decision="approve",
            reason_codes=["LOW_RISK_PROFILE", "POLICY_AUTO_APPROVE"],
        ),
    )


def _auth_required_settings() -> ServiceSettings:
    return ServiceSettings(
        service_name="api-gateway",
        postgres_dsn=DEFAULT_POSTGRES_DSN,
        rabbitmq_url=DEFAULT_RABBITMQ_URL,
        auth_mode="required",
        auth_shared_secret=_AUTH_SHARED_SECRET,
        auth_issuer=_AUTH_ISSUER,
    )


def _bearer_token() -> str:
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "iss": _AUTH_ISSUER,
            "sub": "user-001",
            "scope": "credit_analyst",
            "iat": int(now.timestamp()),
            "nbf": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        },
        _AUTH_SHARED_SECRET,
        algorithm="HS256",
    )
    return f"Bearer {token}"


def _install_fake_evaluation(
    monkeypatch: pytest.MonkeyPatch,
    *,
    service_error: ServiceError | None = None,
) -> None:
    async def _fake_build_credit_evaluation(
        application: Any,
        *,
        settings: ServiceSettings,
    ) -> GatewayCreditEvaluationResponse:
        _ = application
        _ = settings
        if service_error is not None:
            raise service_error
        return _evaluation_response()

    monkeypatch.setattr(gateway_routes, "_build_credit_evaluation", _fake_build_credit_evaluation)


@pytest.mark.unit
def test_gateway_credit_evaluate_pipeline_persists_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_repository = _FakeIdempotencyRepository(reservation=IdempotencyReservation(None))
    _install_fake_repository(monkeypatch, fake_repository)
    _install_fake_evaluation(monkeypatch)

    client = TestClient(app)

    response = client.post(
        "/v1/gateway/credit-evaluate",
        json=_application_payload(),
        headers={"x-idempotency-key": "idem-00000001"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["score"]["model_version"] == "baseline_lr_v1"
    assert payload["score"]["requested_amount"] == 20000
    assert payload["decision"]["decision"] == "approve"
    assert len(fake_repository.reserve_calls) == 1
    reserve_call = fake_repository.reserve_calls[0]
    assert reserve_call[1] == "/v1/gateway/credit-evaluate"
    assert len(reserve_call[2]) == 64
    assert len(fake_repository.persist_calls) == 1
    assert fake_repository.persist_calls[0][2] == 200


@pytest.mark.unit
def test_gateway_credit_evaluate_requires_idempotency_key() -> None:
    client = TestClient(app)

    response = client.post(
        "/v1/gateway/credit-evaluate",
        json=_application_payload(),
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "MISSING_IDEMPOTENCY_KEY"


@pytest.mark.unit
def test_gateway_credit_evaluate_replays_cached_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay_payload = _evaluation_response().model_dump(mode="json")
    fake_repository = _FakeIdempotencyRepository(
        reservation=IdempotencyReservation(replay_payload=replay_payload)
    )
    _install_fake_repository(monkeypatch, fake_repository)

    async def fail_if_called(*_: Any, **__: Any) -> GatewayCreditEvaluationResponse:
        raise AssertionError("credit evaluation should not run on replay")

    monkeypatch.setattr(gateway_routes, "_build_credit_evaluation", fail_if_called)
    client = TestClient(app)

    response = client.post(
        "/v1/gateway/credit-evaluate",
        json=_application_payload(),
        headers={"x-idempotency-key": "idem-00000001"},
    )

    assert response.status_code == 200
    assert response.json() == replay_payload
    assert fake_repository.persist_calls == []


@pytest.mark.unit
def test_gateway_credit_evaluate_returns_conflict_on_mismatched_reuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_repository = _FakeIdempotencyRepository(
        reserve_error=ServiceError(
            error_code="IDEMPOTENCY_REQUEST_MISMATCH",
            message="Idempotency key already exists for a different request payload",
            operation="idempotency_reserve",
            status_code=409,
        )
    )
    _install_fake_repository(monkeypatch, fake_repository)
    client = TestClient(app)

    response = client.post(
        "/v1/gateway/credit-evaluate",
        json=_application_payload(),
        headers={"x-idempotency-key": "idem-00000001"},
    )

    assert response.status_code == 409
    assert response.json()["error_code"] == "IDEMPOTENCY_REQUEST_MISMATCH"


@pytest.mark.unit
def test_gateway_credit_evaluate_persists_replayable_downstream_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_repository = _FakeIdempotencyRepository(reservation=IdempotencyReservation(None))
    _install_fake_repository(monkeypatch, fake_repository)
    _install_fake_evaluation(
        monkeypatch,
        service_error=ServiceError(
            error_code="FEATURE_SERVICE_UNAVAILABLE",
            message="feature service failed",
            operation="feature_materialize",
            status_code=503,
        ),
    )
    client = TestClient(app)

    response = client.post(
        "/v1/gateway/credit-evaluate",
        json=_application_payload(),
        headers={"x-idempotency-key": "idem-00000001"},
    )

    assert response.status_code == 503
    assert response.json()["error_code"] == "FEATURE_SERVICE_UNAVAILABLE"
    assert fake_repository.persist_calls == []
    assert len(fake_repository.persist_failures) == 1
    assert fake_repository.persist_failures[0][2] == 503


@pytest.mark.unit
def test_gateway_credit_evaluate_requires_authorization_when_auth_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_evaluation(monkeypatch)

    def fake_load_settings(_: str) -> ServiceSettings:
        return _auth_required_settings()

    monkeypatch.setattr(gateway_routes, "load_settings", fake_load_settings)
    fake_repository = _FakeIdempotencyRepository(reservation=IdempotencyReservation(None))
    _install_fake_repository(monkeypatch, fake_repository)
    client = TestClient(app)

    response = client.post(
        "/v1/gateway/credit-evaluate",
        json=_application_payload(),
        headers={"x-idempotency-key": "idem-00000001"},
    )

    assert response.status_code == 401
    assert response.json()["error_code"] == "AUTH_MISSING_BEARER_TOKEN"


@pytest.mark.unit
def test_gateway_credit_evaluate_accepts_authorized_request_when_auth_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_repository = _FakeIdempotencyRepository(reservation=IdempotencyReservation(None))
    _install_fake_repository(monkeypatch, fake_repository)
    _install_fake_evaluation(monkeypatch)

    def fake_load_settings(_: str) -> ServiceSettings:
        return _auth_required_settings()

    monkeypatch.setattr(gateway_routes, "load_settings", fake_load_settings)
    client = TestClient(app)

    response = client.post(
        "/v1/gateway/credit-evaluate",
        json=_application_payload(),
        headers={
            "x-idempotency-key": "idem-00000001",
            "authorization": _bearer_token(),
        },
    )

    assert response.status_code == 200
    assert response.json()["decision"]["decision"] == "approve"
