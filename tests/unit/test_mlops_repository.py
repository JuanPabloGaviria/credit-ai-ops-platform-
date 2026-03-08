from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast

import asyncpg
import mlops_service.repositories as mlops_repositories
import pytest

from contracts import (
    EVENT_CREDIT_MODEL_PROMOTED,
    EventEnvelope,
    ModelMetadata,
    PromoteModelRequest,
    RegisterModelRequest,
    TrainingMetrics,
    TrainRunResponse,
)
from shared_kernel import ClaimedOutboxEvent, ServiceError, ServiceSettings
from tests.defaults import (
    DEFAULT_POSTGRES_DSN,
    DEFAULT_RABBITMQ_URL,
    TEST_MODEL_SIGNING_KEY_ID,
)


def _build_settings() -> ServiceSettings:
    return ServiceSettings(
        service_name="mlops",
        postgres_dsn=DEFAULT_POSTGRES_DSN,
        rabbitmq_url=DEFAULT_RABBITMQ_URL,
    )


def _build_metrics() -> TrainingMetrics:
    return TrainingMetrics(
        auc=0.88,
        precision_at_50=0.74,
        recall_at_50=0.69,
        calibration_error=0.03,
    )


def _build_train_run() -> TrainRunResponse:
    return TrainRunResponse(
        run_id="run-12345678",
        model_name="credit-risk",
        dataset_hash="f" * 64,
        random_seed=42,
        algorithm="sklearn_logistic_regression",
        feature_spec_ref="credit-feature-spec/v1",
        training_spec_ref="credit-training-spec/v1",
        artifact_uri="build/mlops/artifacts/run-12345678.json",
        artifact_digest="a" * 64,
        metrics=_build_metrics(),
    )


def _build_training_row() -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "run_id": "run-12345678",
        "model_name": "credit-risk",
        "dataset_hash": "f" * 64,
        "random_seed": 42,
        "algorithm": "sklearn_logistic_regression",
        "feature_spec_ref": "credit-feature-spec/v1",
        "training_spec_ref": "credit-training-spec/v1",
        "artifact_uri": "build/mlops/artifacts/run-12345678.json",
        "artifact_digest": "a" * 64,
        "status": "succeeded",
        "training_metrics": _build_metrics().model_dump(mode="json"),
        "created_at": now,
        "completed_at": now,
    }


def _build_evaluation_row() -> dict[str, object]:
    return {
        "evaluation_id": "eval-12345678",
        "run_id": "run-12345678",
        "evaluation_metrics": _build_metrics().model_dump(mode="json"),
        "passed_policy": True,
        "policy_failures": [],
    }


class _FakeBroker:
    def __init__(self) -> None:
        self.connected = False
        self.closed = False
        self.published_events: list[EventEnvelope] = []

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.closed = True

    async def publish_event(self, event: EventEnvelope) -> None:
        self.published_events.append(event)


class _FakeDbTransaction:
    def __init__(self, db: _FakeDatabase) -> None:
        self._db = db

    async def execute(self, query: str, *params: Any) -> str:
        return await self._db.execute(query, *params)

    async def fetch(self, query: str, *params: Any) -> Sequence[asyncpg.Record]:
        return await self._db.fetch(query, *params)

    async def fetchrow(self, query: str, *params: Any) -> asyncpg.Record | None:
        return await self._db.fetchrow(query, *params)


class _FakeTransactionContext:
    def __init__(self, tx: _FakeDbTransaction) -> None:
        self._tx = tx

    async def __aenter__(self) -> _FakeDbTransaction:
        return self._tx

    async def __aexit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        return None


class _FakeDatabase:
    def __init__(self) -> None:
        self.connected = False
        self.closed = False
        self.execute_results: list[str] = []
        self.fetchrow_results: list[asyncpg.Record | None] = []
        self.fetch_results: list[list[asyncpg.Record]] = []
        self.executed: list[tuple[str, tuple[Any, ...]]] = []

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.closed = True

    async def execute(self, query: str, *params: Any) -> str:
        self.executed.append((query, params))
        if self.execute_results:
            return self.execute_results.pop(0)
        return "INSERT 0 1"

    async def fetchrow(self, query: str, *params: Any) -> asyncpg.Record | None:
        self.executed.append((query, params))
        if self.fetchrow_results:
            return self.fetchrow_results.pop(0)
        return None

    async def fetch(self, query: str, *params: Any) -> Sequence[asyncpg.Record]:
        self.executed.append((query, params))
        if self.fetch_results:
            return self.fetch_results.pop(0)
        return []

    def transaction(self) -> _FakeTransactionContext:
        return _FakeTransactionContext(_FakeDbTransaction(self))


def _build_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[mlops_repositories.MLOpsRepository, _FakeDatabase, _FakeBroker]:
    fake_db = _FakeDatabase()
    fake_broker = _FakeBroker()

    def _db_factory(_dsn: str) -> _FakeDatabase:
        return fake_db

    def _broker_factory(_settings: ServiceSettings) -> _FakeBroker:
        return fake_broker

    monkeypatch.setattr(mlops_repositories, "DatabaseClient", _db_factory)
    monkeypatch.setattr(mlops_repositories, "build_rabbitmq_client", _broker_factory)
    repository = mlops_repositories.MLOpsRepository(_build_settings())
    return repository, fake_db, fake_broker


@pytest.mark.unit
def test_mlops_repository_connect_and_close(monkeypatch: pytest.MonkeyPatch) -> None:
    repository, fake_db, fake_broker = _build_repository(monkeypatch)

    asyncio.run(repository.connect())
    asyncio.run(repository.close())

    assert fake_db.connected is True
    assert fake_db.closed is True
    assert fake_broker.connected is True
    assert fake_broker.closed is True


@pytest.mark.unit
def test_mlops_repository_persist_training_run_executes_insert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, fake_db, _ = _build_repository(monkeypatch)

    asyncio.run(
        repository.persist_training_run(
            run=_build_train_run(),
            dataset_reference="dataset://credit/v1",
        )
    )

    assert fake_db.executed, "expected an INSERT statement for training run persistence"
    last_query, last_params = fake_db.executed[-1]
    assert "INSERT INTO ml_training_runs" in last_query
    assert cast(str, last_params[0]) == "run-12345678"


@pytest.mark.unit
def test_mlops_repository_load_training_run_returns_typed_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, fake_db, _ = _build_repository(monkeypatch)
    fake_db.fetchrow_results.append(cast(asyncpg.Record, _build_training_row()))

    result = asyncio.run(repository.load_training_run("run-12345678"))

    assert result.run_id == "run-12345678"
    assert result.model_name == "credit-risk"
    assert abs(result.metrics.auc - 0.88) < 1e-9


@pytest.mark.unit
def test_mlops_repository_load_training_run_raises_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, _, _ = _build_repository(monkeypatch)

    with pytest.raises(ServiceError) as error:
        asyncio.run(repository.load_training_run("run-missing"))

    assert error.value.error_code == "ML_RUN_NOT_FOUND"


@pytest.mark.unit
def test_mlops_repository_persist_and_load_evaluation_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, fake_db, _ = _build_repository(monkeypatch)

    persisted = asyncio.run(
        repository.persist_evaluation_run(
            run_id="run-12345678",
            metrics=_build_metrics(),
            passed_policy=True,
            policy_failures=[],
        )
    )
    assert persisted.run_id == "run-12345678"

    fake_db.fetchrow_results.append(cast(asyncpg.Record, _build_evaluation_row()))
    loaded = asyncio.run(repository.load_evaluation_run("eval-12345678"))
    assert loaded.evaluation_id == "eval-12345678"
    assert loaded.passed_policy is True


@pytest.mark.unit
def test_mlops_repository_load_evaluation_run_raises_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, _, _ = _build_repository(monkeypatch)

    with pytest.raises(ServiceError) as error:
        asyncio.run(repository.load_evaluation_run("eval-missing"))

    assert error.value.error_code == "ML_EVALUATION_NOT_FOUND"


@pytest.mark.unit
def test_mlops_repository_register_model_candidate_returns_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, fake_db, _ = _build_repository(monkeypatch)
    fake_db.fetchrow_results.append(cast(asyncpg.Record, _build_training_row()))
    loaded_run = asyncio.run(repository.load_training_run("run-12345678"))

    fake_db.fetchrow_results.append(cast(asyncpg.Record, _build_evaluation_row()))
    loaded_evaluation = asyncio.run(repository.load_evaluation_run("eval-12345678"))

    request = RegisterModelRequest(
        model_name="credit-risk",
        model_version="v1.0.0",
        run_id="run-12345678",
        evaluation_id="eval-12345678",
    )
    metadata = ModelMetadata(
        model_name="credit-risk",
        model_version="v1.0.0",
        dataset_hash="f" * 64,
        random_seed=42,
        environment_fingerprint="env-fingerprint-123",
    )

    response = asyncio.run(
        repository.register_model_candidate(
            request=request,
            run=loaded_run,
            evaluation=loaded_evaluation,
            metadata=metadata,
            signed_artifact_uri="build/mlops/registered_artifacts/credit-risk-v1.0.0.json",
            signed_artifact_digest="c" * 64,
            signature_algorithm="ed25519",
            signature_key_id=TEST_MODEL_SIGNING_KEY_ID,
            artifact_signature="d" * 88,
            model_card_uri="build/mlops/model-cards/credit-risk-v1.0.0.json",
            model_card_checksum="b" * 64,
            environment_snapshot={"python": "3.11.15"},
        )
    )

    assert response.status == "candidate"
    last_query, _ = fake_db.executed[-1]
    assert "INSERT INTO model_registry" in last_query


@pytest.mark.unit
def test_mlops_repository_promote_model_raises_not_found_when_update_misses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, fake_db, _ = _build_repository(monkeypatch)
    fake_db.execute_results = ["INSERT 0 0"]
    request = PromoteModelRequest(
        model_name="credit-risk",
        model_version="v1.0.0",
        stage="production",
        approved_by="approver-001",
        approval_ticket="ticket-001",
        risk_signoff_ref="risk-001",
    )

    with pytest.raises(ServiceError) as error:
        asyncio.run(
            repository.promote_model(
                request=request,
                trace_id="trace-12345678",
                db=fake_db,
            )
        )

    assert error.value.error_code == "MODEL_VERSION_NOT_FOUND"


@pytest.mark.unit
def test_mlops_repository_promote_model_persists_event_and_records_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, fake_db, _ = _build_repository(monkeypatch)
    fake_db.execute_results = ["INSERT 0 1", "INSERT 0 1"]
    request = PromoteModelRequest(
        model_name="credit-risk",
        model_version="v1.0.0",
        stage="staging",
        approved_by="approver-001",
        approval_ticket="ticket-001",
        risk_signoff_ref="risk-001",
    )

    response = asyncio.run(
        repository.promote_model(
            request=request,
            trace_id="trace-12345678",
        )
    )

    assert response.model_name == "credit-risk"
    assert response.event_id
    assert any("INSERT INTO model_stage_assignments" in query for query, _ in fake_db.executed)
    assert any("INSERT INTO mlops_outbox" in query for query, _ in fake_db.executed)


@pytest.mark.unit
def test_mlops_repository_flush_outbox_publishes_and_marks(monkeypatch: pytest.MonkeyPatch) -> None:
    repository, _, fake_broker = _build_repository(monkeypatch)
    sample_event = EventEnvelope(
        event_name=EVENT_CREDIT_MODEL_PROMOTED,
        event_id="event-12345678",
        trace_id="trace-12345678",
        producer="mlops-service",
        payload={
            "model_name": "credit-risk",
            "model_version": "v1.0.0",
            "stage": "production",
            "promoted_at": datetime.now(UTC).isoformat(),
        },
    )
    marked_event_ids: list[tuple[str, str]] = []

    async def _fake_fetch_pending(
        _db: object,
        _table_name: str,
        *,
        lease_seconds: int,
    ) -> list[ClaimedOutboxEvent]:
        _ = lease_seconds
        return [
            ClaimedOutboxEvent(
                event=sample_event,
                claim_token="-".join(("claim", "123")),
                publish_attempts=1,
            )
        ]

    async def _fake_mark_published(
        _db: object,
        _table_name: str,
        event_id: str,
        *,
        claim_token: str,
    ) -> None:
        marked_event_ids.append((event_id, claim_token))

    monkeypatch.setattr(mlops_repositories, "fetch_pending_outbox_events", _fake_fetch_pending)
    monkeypatch.setattr(
        mlops_repositories,
        "mark_outbox_event_published",
        _fake_mark_published,
    )

    published = asyncio.run(repository.flush_outbox())

    assert published == 1
    assert len(fake_broker.published_events) == 1
    assert marked_event_ids == [("event-12345678", "claim-123")]
