from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any, cast

import asyncpg
import collab_assistant.repositories as assistant_repositories
import pytest

from contracts import (
    EVENT_CREDIT_ASSISTANT_SUMMARIZED,
    AssistantSummaryRequest,
    AssistantSummaryResponse,
    EventEnvelope,
)
from shared_kernel import ClaimedOutboxEvent, ServiceError, ServiceSettings
from tests.defaults import DEFAULT_POSTGRES_DSN, DEFAULT_RABBITMQ_URL


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


def _settings() -> ServiceSettings:
    return ServiceSettings(
        service_name="collab-assistant",
        postgres_dsn=DEFAULT_POSTGRES_DSN,
        rabbitmq_url=DEFAULT_RABBITMQ_URL,
    )


def _install_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[assistant_repositories.AssistantRepository, _FakeDatabase, _FakeBroker]:
    fake_db = _FakeDatabase()
    fake_broker = _FakeBroker()

    def _db_factory(_dsn: str) -> _FakeDatabase:
        return fake_db

    def _broker_factory(_settings: ServiceSettings) -> _FakeBroker:
        return fake_broker

    monkeypatch.setattr(assistant_repositories, "DatabaseClient", _db_factory)
    monkeypatch.setattr(assistant_repositories, "build_rabbitmq_client", _broker_factory)
    repository = assistant_repositories.AssistantRepository(_settings())
    return repository, fake_db, fake_broker


def _decision_event() -> EventEnvelope:
    return EventEnvelope(
        event_name="credit.decision.made.v1",
        event_id="event-decision-123",
        trace_id="trace-decision-123",
        producer="decision-service",
        payload={
            "application_id": "app-12345678",
            "decision": "review",
            "risk_score": 0.42,
            "reason_codes": ["HIGH_DTI"],
        },
    )


@pytest.mark.unit
def test_assistant_repository_connect_and_close(monkeypatch: pytest.MonkeyPatch) -> None:
    repository, fake_db, fake_broker = _install_repository(monkeypatch)

    asyncio.run(repository.connect())
    asyncio.run(repository.close())

    assert fake_db.connected is True
    assert fake_db.closed is True
    assert fake_broker.connected is True
    assert fake_broker.closed is True


@pytest.mark.unit
def test_assistant_repository_summarize_request_persists_and_enqueues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, fake_db, _ = _install_repository(monkeypatch)
    request = AssistantSummaryRequest(
        application_id="app-12345678",
        decision="review",
        risk_score=0.52,
        reason_codes=["HIGH_DTI"],
    )

    response = asyncio.run(
        repository.summarize_request(
            request=request,
            trace_id="trace-assistant-123",
        )
    )

    assert response.application_id == request.application_id
    assert response.mode == "deterministic"
    assert any("INSERT INTO assistant_summary_history" in query for query, _ in fake_db.executed)
    assert not any("INSERT INTO assistant_summaries" in query for query, _ in fake_db.executed)
    assert any("INSERT INTO assistant_outbox" in query for query, _ in fake_db.executed)


@pytest.mark.unit
def test_assistant_repository_get_summary_success_and_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, fake_db, _ = _install_repository(monkeypatch)
    fake_db.fetchrow_results.append(
        cast(
            asyncpg.Record,
            {
                "application_id": "app-12345678",
                "mode": "deterministic",
                "summary": "summary text",
            },
        )
    )

    summary = asyncio.run(repository.get_summary("app-12345678"))

    assert summary == AssistantSummaryResponse(
        application_id="app-12345678",
        mode="deterministic",
        summary="summary text",
    )

    with pytest.raises(ServiceError) as error:
        asyncio.run(repository.get_summary("app-missing"))

    assert error.value.error_code == "ASSISTANT_SUMMARY_NOT_FOUND"


@pytest.mark.unit
def test_assistant_repository_handle_decision_event_first_seen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, _, _ = _install_repository(monkeypatch)
    summarized_requests: list[tuple[AssistantSummaryRequest, str]] = []

    async def _record_first_seen(*_args: object, **_kwargs: object) -> bool:
        return True

    async def _summarize(
        request: AssistantSummaryRequest,
        trace_id: str,
        db: object | None = None,
        *,
        source_event_id: str | None = None,
    ) -> AssistantSummaryResponse:
        _ = db
        _ = source_event_id
        summarized_requests.append((request, trace_id))
        return AssistantSummaryResponse(
            application_id=request.application_id,
            mode="deterministic",
            summary="generated",
        )

    async def _flush_outbox() -> int:
        return 0

    monkeypatch.setattr(assistant_repositories, "record_inbox_event", _record_first_seen)
    monkeypatch.setattr(repository, "summarize_request", _summarize)
    monkeypatch.setattr(repository, "flush_outbox", _flush_outbox)

    handled = asyncio.run(repository.handle_decision_event(_decision_event()))

    assert handled is True
    assert len(summarized_requests) == 1
    assert summarized_requests[0][0].application_id == "app-12345678"
    assert summarized_requests[0][1] == "trace-decision-123"


@pytest.mark.unit
def test_assistant_repository_handle_decision_event_duplicate_no_outbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, _, _ = _install_repository(monkeypatch)

    async def _record_duplicate(*_args: object, **_kwargs: object) -> bool:
        return False

    async def _summarize_should_not_run(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("summarize_request must not run for duplicate inbox events")

    monkeypatch.setattr(assistant_repositories, "record_inbox_event", _record_duplicate)
    monkeypatch.setattr(repository, "summarize_request", _summarize_should_not_run)

    handled = asyncio.run(repository.handle_decision_event(_decision_event()))

    assert handled is False


@pytest.mark.unit
def test_assistant_repository_flush_outbox_publishes_and_marks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, _, fake_broker = _install_repository(monkeypatch)
    marked_event_ids: list[tuple[str, str]] = []
    first_claim_token = "-".join(("claim", "assistant", "001"))
    second_claim_token = "-".join(("claim", "assistant", "002"))
    events = [
        ClaimedOutboxEvent(
            event=EventEnvelope(
                event_name=EVENT_CREDIT_ASSISTANT_SUMMARIZED,
                event_id="event-assistant-001",
                trace_id="trace-assistant-001",
                producer="collab-assistant-service",
                payload={"application_id": "app-001", "summary": "one", "mode": "deterministic"},
            ),
            claim_token=first_claim_token,
            publish_attempts=1,
        ),
        ClaimedOutboxEvent(
            event=EventEnvelope(
                event_name=EVENT_CREDIT_ASSISTANT_SUMMARIZED,
                event_id="event-assistant-002",
                trace_id="trace-assistant-002",
                producer="collab-assistant-service",
                payload={"application_id": "app-002", "summary": "two", "mode": "deterministic"},
            ),
            claim_token=second_claim_token,
            publish_attempts=1,
        ),
    ]

    async def _fetch_pending(
        _db: object,
        _table: str,
        *,
        lease_seconds: int,
    ) -> list[ClaimedOutboxEvent]:
        _ = lease_seconds
        return events

    async def _mark_published(
        _db: object,
        _table: str,
        event_id: str,
        *,
        claim_token: str,
    ) -> None:
        marked_event_ids.append((event_id, claim_token))

    monkeypatch.setattr(assistant_repositories, "fetch_pending_outbox_events", _fetch_pending)
    monkeypatch.setattr(assistant_repositories, "mark_outbox_event_published", _mark_published)

    published = asyncio.run(repository.flush_outbox())

    assert published == 2
    assert [event.event_id for event in fake_broker.published_events] == [
        "event-assistant-001",
        "event-assistant-002",
    ]
    assert marked_event_ids == [
        ("event-assistant-001", "claim-assistant-001"),
        ("event-assistant-002", "claim-assistant-002"),
    ]
