from __future__ import annotations

import os
from datetime import UTC, datetime

import mlops_service.routes as mlops_routes
import pytest
from fastapi.testclient import TestClient

from contracts import (
    EvaluateRunResponse,
    MLOpsRunResponse,
    ModelMetadata,
    PromoteModelRequest,
    PromoteModelResponse,
    RegisterModelRequest,
    RegisterModelResponse,
    TrainingMetrics,
    TrainRunResponse,
)
from tests.defaults import TEST_MODEL_SIGNING_KEY_ID, TEST_MODEL_SIGNING_PRIVATE_KEY_PEM

os.environ.setdefault("MODEL_SIGNING_PRIVATE_KEY_PEM", TEST_MODEL_SIGNING_PRIVATE_KEY_PEM)
os.environ.setdefault("MODEL_SIGNING_KEY_ID", TEST_MODEL_SIGNING_KEY_ID)

from mlops_service.main import app


class _FakeMLOpsRepository:
    def __init__(self) -> None:
        self.persisted_run: TrainRunResponse | None = None
        self.persisted_dataset_reference: str | None = None
        self.training_run = MLOpsRunResponse(
            run_id="run-12345678",
            model_name="credit-risk",
            dataset_hash="0" * 64,
            random_seed=42,
            algorithm="sklearn_logistic_regression",
            feature_spec_ref="credit-feature-spec/v1",
            training_spec_ref="credit-training-spec/v1",
            artifact_uri="build/mlops/artifacts/run-12345678.json",
            artifact_digest="1" * 64,
            status="succeeded",
            metrics=TrainingMetrics(
                auc=0.86,
                precision_at_50=0.77,
                recall_at_50=0.73,
                calibration_error=0.03,
            ),
            created_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
        )
        self.evaluation_run = EvaluateRunResponse(
            evaluation_id="eval-12345678",
            run_id="run-12345678",
            metrics=self.training_run.metrics,
            passed_policy=True,
            policy_failures=[],
        )
        self.promotion_response = PromoteModelResponse(
            model_name="credit-risk",
            model_version="v1.0.0",
            stage="production",
            promoted_at=datetime.now(UTC),
            event_id="event-12345678",
        )

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def persist_training_run(self, *, run: TrainRunResponse, dataset_reference: str) -> None:
        self.persisted_run = run
        self.persisted_dataset_reference = dataset_reference

    async def load_training_run(self, run_id: str) -> MLOpsRunResponse:
        _ = run_id
        return self.training_run

    async def persist_evaluation_run(
        self,
        *,
        run_id: str,
        metrics: TrainingMetrics,
        passed_policy: bool,
        policy_failures: list[str],
    ) -> EvaluateRunResponse:
        self.evaluation_run = EvaluateRunResponse(
            evaluation_id="eval-12345678",
            run_id=run_id,
            metrics=metrics,
            passed_policy=passed_policy,
            policy_failures=policy_failures,
        )
        return self.evaluation_run

    async def load_evaluation_run(self, evaluation_id: str) -> EvaluateRunResponse:
        _ = evaluation_id
        return self.evaluation_run

    async def register_model_candidate(
        self,
        *,
        request: RegisterModelRequest,
        run: MLOpsRunResponse,
        evaluation: EvaluateRunResponse,
        metadata: ModelMetadata,
        signed_artifact_uri: str,
        signed_artifact_digest: str,
        signature_algorithm: str,
        signature_key_id: str,
        artifact_signature: str,
        model_card_uri: str,
        model_card_checksum: str,
        environment_snapshot: dict[str, str],
    ) -> RegisterModelResponse:
        _ = run
        _ = evaluation
        _ = signed_artifact_uri
        _ = signed_artifact_digest
        _ = signature_algorithm
        _ = signature_key_id
        _ = artifact_signature
        _ = model_card_checksum
        _ = environment_snapshot
        return RegisterModelResponse(
            model_name=request.model_name,
            model_version=request.model_version,
            status="candidate",
            model_card_uri=model_card_uri,
            metadata=metadata,
        )

    async def promote_model(
        self,
        *,
        request: PromoteModelRequest,
        trace_id: str,
    ) -> PromoteModelResponse:
        _ = trace_id
        return PromoteModelResponse(
            model_name=request.model_name,
            model_version=request.model_version,
            stage=request.stage,
            promoted_at=self.promotion_response.promoted_at,
            event_id=self.promotion_response.event_id,
        )


def _install_fake_repository(
    monkeypatch: pytest.MonkeyPatch,
    fake_repository: _FakeMLOpsRepository,
) -> None:
    def _repository_factory(settings: object) -> _FakeMLOpsRepository:
        _ = settings
        return fake_repository

    monkeypatch.setattr(mlops_routes, "MLOpsRepository", _repository_factory)


def _idempotency_headers() -> dict[str, str]:
    return {"x-idempotency-key": "-".join(("idem", "test", "request", "0001"))}


@pytest.mark.unit
def test_mlops_train_requires_idempotency_header() -> None:
    client = TestClient(app)

    response = client.post(
        "/v1/mlops/train",
        json={
            "model_name": "credit-risk",
            "dataset_reference": "dataset://credit/v1",
            "random_seed": 42,
        },
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "MISSING_IDEMPOTENCY_KEY"


@pytest.mark.unit
def test_mlops_train_persists_training_run(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_repository = _FakeMLOpsRepository()
    _install_fake_repository(monkeypatch, fake_repository)
    client = TestClient(app)

    response = client.post(
        "/v1/mlops/train",
        json={
            "model_name": "credit-risk",
            "dataset_reference": "dataset://credit/v1",
            "random_seed": 42,
        },
        headers=_idempotency_headers(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["dataset_hash"]
    assert fake_repository.persisted_run is not None
    assert fake_repository.persisted_dataset_reference == "dataset://credit/v1"


@pytest.mark.unit
def test_mlops_register_rejects_run_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_repository = _FakeMLOpsRepository()
    fake_repository.evaluation_run = EvaluateRunResponse(
        evaluation_id="eval-12345678",
        run_id="run-different",
        metrics=fake_repository.training_run.metrics,
        passed_policy=True,
        policy_failures=[],
    )
    _install_fake_repository(monkeypatch, fake_repository)
    client = TestClient(app)

    response = client.post(
        "/v1/mlops/register",
        json={
            "model_name": "credit-risk",
            "model_version": "v1.0.0",
            "run_id": "run-12345678",
            "evaluation_id": "eval-12345678",
        },
        headers=_idempotency_headers(),
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "ML_RUN_EVALUATION_MISMATCH"


@pytest.mark.unit
def test_mlops_register_rejects_policy_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_repository = _FakeMLOpsRepository()
    fake_repository.evaluation_run = EvaluateRunResponse(
        evaluation_id="eval-12345678",
        run_id="run-12345678",
        metrics=fake_repository.training_run.metrics,
        passed_policy=False,
        policy_failures=["auc_below_threshold"],
    )
    _install_fake_repository(monkeypatch, fake_repository)
    client = TestClient(app)

    response = client.post(
        "/v1/mlops/register",
        json={
            "model_name": "credit-risk",
            "model_version": "v1.0.0",
            "run_id": "run-12345678",
            "evaluation_id": "eval-12345678",
        },
        headers=_idempotency_headers(),
    )

    assert response.status_code == 409
    assert response.json()["error_code"] == "ML_POLICY_GATE_FAILED"


@pytest.mark.unit
def test_mlops_promote_returns_event_id(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_repository = _FakeMLOpsRepository()
    _install_fake_repository(monkeypatch, fake_repository)
    client = TestClient(app)

    response = client.post(
        "/v1/mlops/promote",
        json={
            "model_name": "credit-risk",
            "model_version": "v1.0.0",
            "stage": "production",
            "approved_by": "approver-001",
            "approval_ticket": "ticket-001",
            "risk_signoff_ref": "risk-001",
        },
        headers=_idempotency_headers(),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["event_id"] == "event-12345678"
