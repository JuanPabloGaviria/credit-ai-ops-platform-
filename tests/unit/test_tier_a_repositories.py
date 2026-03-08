from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any, cast

import application_service.repositories as application_repositories
import asyncpg
import decision_service.repositories as decision_repositories
import feature_service.repositories as feature_repositories
import pytest
import scoring_service.repositories as scoring_repositories

from contracts import (
    EVENT_CREDIT_APPLICATION_SUBMITTED,
    EVENT_CREDIT_FEATURE_MATERIALIZED,
    EVENT_CREDIT_SCORING_GENERATED,
    ApplicationInput,
    EventEnvelope,
    FeatureVector,
    ScorePrediction,
)
from shared_kernel import ClaimedOutboxEvent, ServiceSettings
from tests.defaults import DEFAULT_POSTGRES_DSN, DEFAULT_RABBITMQ_URL


def _build_settings(service_name: str) -> ServiceSettings:
    return ServiceSettings(
        service_name=service_name,
        postgres_dsn=DEFAULT_POSTGRES_DSN,
        rabbitmq_url=DEFAULT_RABBITMQ_URL,
    )


def _build_application() -> ApplicationInput:
    return ApplicationInput(
        application_id="app-12345678",
        applicant_id="applicant-12345678",
        monthly_income=5200.0,
        monthly_debt=1800.0,
        requested_amount=25000.0,
        credit_history_months=48,
        existing_defaults=0,
    )


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


class _FakeScoringModel:
    def __init__(self, model_version: str = "v1.0.0") -> None:
        self.model_version = model_version
        self.scored_features: list[FeatureVector] = []

    def score(self, features: FeatureVector) -> ScorePrediction:
        self.scored_features.append(features)
        return ScorePrediction(
            application_id=features.application_id,
            requested_amount=features.requested_amount,
            risk_score=0.31,
            model_version=self.model_version,
            reason_codes=["LOW_RISK_PROFILE"],
        )


class _FakeDbExecutor:
    def __init__(self, db: _FakeDatabase) -> None:
        self._db = db

    async def execute(self, query: str, *args: Any) -> str:
        return await self._db.execute(query, *args)

    async def fetch(self, query: str, *args: Any) -> Sequence[asyncpg.Record]:
        return await self._db.fetch(query, *args)

    async def fetchrow(self, query: str, *args: Any) -> asyncpg.Record | None:
        return await self._db.fetchrow(query, *args)


class _FakeTxContext:
    def __init__(self, executor: _FakeDbExecutor) -> None:
        self._executor = executor

    async def __aenter__(self) -> _FakeDbExecutor:
        return self._executor

    async def __aexit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        return None


class _FakeDatabase:
    def __init__(self) -> None:
        self.connected = False
        self.closed = False
        self.executed: list[tuple[str, tuple[Any, ...]]] = []
        self.fetch_results: list[list[asyncpg.Record]] = []
        self.fetchrow_results: list[asyncpg.Record | None] = []

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.closed = True

    async def execute(self, query: str, *args: Any) -> str:
        self.executed.append((query, args))
        return "INSERT 0 1"

    async def fetch(self, query: str, *args: Any) -> Sequence[asyncpg.Record]:
        self.executed.append((query, args))
        if self.fetch_results:
            return self.fetch_results.pop(0)
        return []

    async def fetchrow(self, query: str, *args: Any) -> asyncpg.Record | None:
        self.executed.append((query, args))
        if self.fetchrow_results:
            return self.fetchrow_results.pop(0)
        return None

    def transaction(self) -> _FakeTxContext:
        return _FakeTxContext(_FakeDbExecutor(self))


def _install_repo_fixtures(
    monkeypatch: pytest.MonkeyPatch,
    module: object,
    service_name: str,
) -> tuple[Any, _FakeDatabase, _FakeBroker]:
    fake_db = _FakeDatabase()
    fake_broker = _FakeBroker()

    def _db_factory(_dsn: str) -> _FakeDatabase:
        return fake_db

    def _broker_factory(_settings: ServiceSettings) -> _FakeBroker:
        return fake_broker

    monkeypatch.setattr(module, "DatabaseClient", _db_factory)
    monkeypatch.setattr(module, "build_rabbitmq_client", _broker_factory)
    repository_class = cast(type[Any], getattr(module, f"{service_name.title()}Repository"))
    repository = repository_class(_build_settings(service_name))
    return repository, fake_db, fake_broker


@pytest.mark.unit
def test_application_repository_core_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    repository, fake_db, fake_broker = _install_repo_fixtures(
        monkeypatch,
        application_repositories,
        "application",
    )
    application = _build_application()
    captured_events: list[EventEnvelope] = []
    marked_ids: list[str] = []
    outbox_event = EventEnvelope(
        event_name=EVENT_CREDIT_APPLICATION_SUBMITTED,
        event_id="event-application-123",
        trace_id="trace-application-123",
        producer="application-service",
        payload=application.model_dump(mode="json"),
    )

    async def _capture_enqueue(_db: object, _table: str, event: EventEnvelope) -> None:
        captured_events.append(event)

    async def _fetch_pending(
        _db: object,
        _table: str,
        *,
        lease_seconds: int,
    ) -> list[ClaimedOutboxEvent]:
        _ = lease_seconds
        claim_token = "-".join(("claim", "application", "123"))
        return [
            ClaimedOutboxEvent(
                event=outbox_event,
                claim_token=claim_token,
                publish_attempts=1,
            )
        ]

    async def _mark_published(
        _db: object,
        _table: str,
        event_id: str,
        *,
        claim_token: str,
    ) -> None:
        _ = claim_token
        marked_ids.append(event_id)

    monkeypatch.setattr(application_repositories, "enqueue_outbox_event", _capture_enqueue)
    monkeypatch.setattr(application_repositories, "fetch_pending_outbox_events", _fetch_pending)
    monkeypatch.setattr(application_repositories, "mark_outbox_event_published", _mark_published)

    asyncio.run(repository.connect())
    emitted_event_id = asyncio.run(
        repository.intake_application(
            application=application,
            trace_id="trace-application-123",
        )
    )
    published = asyncio.run(repository.flush_outbox())
    asyncio.run(repository.close())

    assert emitted_event_id
    assert captured_events and captured_events[0].event_id == emitted_event_id
    assert captured_events[0].correlation_id == "trace-application-123"
    assert captured_events[0].causation_id is None
    assert any("INSERT INTO application_submissions" in query for query, _ in fake_db.executed)
    assert not any("INSERT INTO applications" in query for query, _ in fake_db.executed)
    assert published == 1
    assert marked_ids == ["event-application-123"]
    assert fake_broker.connected is True
    assert fake_broker.closed is True


@pytest.mark.unit
def test_feature_repository_core_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    repository, fake_db, _ = _install_repo_fixtures(monkeypatch, feature_repositories, "feature")
    application = _build_application()

    async def _record_inbox(_db: object, _table: str, _event: EventEnvelope) -> bool:
        return True

    monkeypatch.setattr(feature_repositories, "record_inbox_event", _record_inbox)

    event = EventEnvelope(
        event_name=EVENT_CREDIT_APPLICATION_SUBMITTED,
        event_id="event-application-123",
        trace_id="trace-feature-123",
        producer="application-service",
        payload=application.model_dump(mode="json"),
    )
    processed = asyncio.run(repository.handle_submitted_event(event))

    assert processed is True
    assert any("INSERT INTO feature_vector_history" in query for query, _ in fake_db.executed)
    assert not any("INSERT INTO feature_vectors" in query for query, _ in fake_db.executed)


@pytest.mark.unit
def test_scoring_repository_core_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    repository, fake_db, _ = _install_repo_fixtures(monkeypatch, scoring_repositories, "scoring")
    fake_scoring_model = _FakeScoringModel()
    feature_vector = FeatureVector(
        application_id="app-12345678",
        requested_amount=25000.0,
        debt_to_income=0.35,
        amount_to_income=4.8,
        credit_history_months=48,
        existing_defaults=0,
    )

    async def _record_inbox(_db: object, _table: str, _event: EventEnvelope) -> bool:
        return True

    monkeypatch.setattr(scoring_repositories, "record_inbox_event", _record_inbox)
    async def _resolve_active_model(*, db: object, settings: ServiceSettings) -> _FakeScoringModel:
        _ = db
        _ = settings
        return fake_scoring_model

    monkeypatch.setattr(scoring_repositories, "resolve_active_scoring_model", _resolve_active_model)

    event = EventEnvelope(
        event_name=EVENT_CREDIT_FEATURE_MATERIALIZED,
        event_id="event-feature-123",
        trace_id="trace-scoring-123",
        producer="feature-service",
        payload=feature_vector.model_dump(mode="json"),
    )
    processed = asyncio.run(repository.handle_feature_event(event))

    assert processed is True
    assert fake_scoring_model.scored_features == [feature_vector]
    assert any("INSERT INTO score_prediction_history" in query for query, _ in fake_db.executed)
    assert not any("INSERT INTO score_predictions" in query for query, _ in fake_db.executed)


@pytest.mark.unit
def test_decision_repository_core_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    repository, fake_db, _ = _install_repo_fixtures(monkeypatch, decision_repositories, "decision")
    score = ScorePrediction(
        application_id="app-12345678",
        requested_amount=25000.0,
        risk_score=0.44,
        model_version="v1.0",
        reason_codes=["POLICY_AUTO_APPROVE"],
    )

    async def _record_inbox(_db: object, _table: str, _event: EventEnvelope) -> bool:
        return True

    monkeypatch.setattr(decision_repositories, "record_inbox_event", _record_inbox)

    event = EventEnvelope(
        event_name=EVENT_CREDIT_SCORING_GENERATED,
        event_id="event-scoring-123",
        trace_id="trace-decision-123",
        producer="scoring-service",
        payload=score.model_dump(mode="json"),
    )
    processed = asyncio.run(repository.handle_score_event(event))

    assert processed is True
    assert any("INSERT INTO credit_decision_history" in query for query, _ in fake_db.executed)
    assert not any("INSERT INTO credit_decisions" in query for query, _ in fake_db.executed)


@pytest.mark.unit
def test_tier_a_flush_outbox_returns_zero_when_no_pending_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fetch_none(
        _db: object,
        _table: str,
        *,
        lease_seconds: int,
    ) -> list[ClaimedOutboxEvent]:
        _ = lease_seconds
        return []

    monkeypatch.setattr(application_repositories, "fetch_pending_outbox_events", _fetch_none)
    monkeypatch.setattr(feature_repositories, "fetch_pending_outbox_events", _fetch_none)
    monkeypatch.setattr(scoring_repositories, "fetch_pending_outbox_events", _fetch_none)
    monkeypatch.setattr(decision_repositories, "fetch_pending_outbox_events", _fetch_none)

    application_repo, _, _ = _install_repo_fixtures(
        monkeypatch,
        application_repositories,
        "application",
    )
    feature_repo, _, _ = _install_repo_fixtures(monkeypatch, feature_repositories, "feature")
    scoring_repo, _, _ = _install_repo_fixtures(monkeypatch, scoring_repositories, "scoring")
    decision_repo, _, _ = _install_repo_fixtures(monkeypatch, decision_repositories, "decision")

    assert asyncio.run(application_repo.flush_outbox()) == 0
    assert asyncio.run(feature_repo.flush_outbox()) == 0
    assert asyncio.run(scoring_repo.flush_outbox()) == 0
    assert asyncio.run(decision_repo.flush_outbox()) == 0
