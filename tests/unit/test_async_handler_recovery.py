import asyncio
from dataclasses import dataclass

import pytest
from collab_assistant.repositories import AssistantRepository
from decision_service.repositories import DecisionRepository
from feature_service.repositories import FeatureRepository
from scoring_service.repositories import ScoringRepository

from contracts import EventEnvelope
from shared_kernel import ServiceSettings


@dataclass(slots=True)
class _DummyTransaction:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(
        self,
        _exc_type: object,
        _exc: object,
        _tb: object,
    ) -> None:
        return None


class _DummyDb:
    def transaction(self) -> _DummyTransaction:
        return _DummyTransaction()


def _fake_transaction(_self: object) -> _DummyTransaction:
    return _DummyDb().transaction()


def _settings(service_name: str) -> ServiceSettings:
    return ServiceSettings(
        service_name=service_name,
        postgres_dsn="postgresql://db.example:5432/credit_ai_ops",
        rabbitmq_url="amqp://mq.example:5672/",
    )


def _event() -> EventEnvelope:
    return EventEnvelope(
        event_name="credit.application.submitted.v1",
        event_id="event-duplicate-0001",
        trace_id="trace-duplicate-0001",
        producer="unit-test",
        payload={"application_id": "app-0001"},
    )


@pytest.mark.unit
def test_feature_handler_returns_false_for_duplicate_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = FeatureRepository(_settings("feature"))
    monkeypatch.setattr(
        "feature_service.repositories.DatabaseClient.transaction",
        _fake_transaction,
    )

    async def duplicate_inbox(*_args: object, **_kwargs: object) -> bool:
        return False

    async def should_not_materialize(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("materialization should not run for duplicate inbox events")

    monkeypatch.setattr("feature_service.repositories.record_inbox_event", duplicate_inbox)
    monkeypatch.setattr(repository, "materialize_from_application", should_not_materialize)

    handled = asyncio.run(repository.handle_submitted_event(_event()))

    assert handled is False


@pytest.mark.unit
def test_scoring_handler_returns_false_for_duplicate_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = ScoringRepository(_settings("scoring"))
    monkeypatch.setattr(
        "scoring_service.repositories.DatabaseClient.transaction",
        _fake_transaction,
    )

    async def duplicate_inbox(*_args: object, **_kwargs: object) -> bool:
        return False

    async def should_not_score(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("scoring should not run for duplicate inbox events")

    monkeypatch.setattr("scoring_service.repositories.record_inbox_event", duplicate_inbox)
    monkeypatch.setattr(repository, "score_features", should_not_score)

    handled = asyncio.run(repository.handle_feature_event(_event()))

    assert handled is False


@pytest.mark.unit
def test_decision_handler_returns_false_for_duplicate_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = DecisionRepository(_settings("decision"))
    monkeypatch.setattr(
        "decision_service.repositories.DatabaseClient.transaction",
        _fake_transaction,
    )

    async def duplicate_inbox(*_args: object, **_kwargs: object) -> bool:
        return False

    async def should_not_decide(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("decisioning should not run for duplicate inbox events")

    monkeypatch.setattr("decision_service.repositories.record_inbox_event", duplicate_inbox)
    monkeypatch.setattr(repository, "decide_from_score", should_not_decide)

    handled = asyncio.run(repository.handle_score_event(_event()))

    assert handled is False


@pytest.mark.unit
def test_assistant_handler_returns_false_for_duplicate_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = AssistantRepository(_settings("collab-assistant"))
    monkeypatch.setattr(
        "collab_assistant.repositories.DatabaseClient.transaction",
        _fake_transaction,
    )

    async def duplicate_inbox(*_args: object, **_kwargs: object) -> bool:
        return False

    async def should_not_summarize(*_args: object, **_kwargs: object) -> object:
        raise AssertionError(
            "assistant summary generation should not run for duplicate inbox events"
        )

    monkeypatch.setattr("collab_assistant.repositories.record_inbox_event", duplicate_inbox)
    monkeypatch.setattr(repository, "summarize_request", should_not_summarize)

    handled = asyncio.run(repository.handle_decision_event(_event()))

    assert handled is False
