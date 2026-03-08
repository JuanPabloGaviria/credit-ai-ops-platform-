from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import observability_audit.repositories as audit_repositories
import pytest

from contracts import EventEnvelope
from shared_kernel import ServiceError
from tests.defaults import DEFAULT_POSTGRES_DSN


class _FakeTransaction:
    def __init__(self, db: _FakeDatabase) -> None:
        self._db = db

    async def execute(self, query: str, *params: object) -> str:
        return await self._db.execute(query, *params)

    async def fetch(self, query: str, *params: object) -> list[dict[str, object]]:
        return await self._db.fetch(query, *params)

    async def fetchrow(self, query: str, *params: object) -> dict[str, object] | None:
        return await self._db.fetchrow(query, *params)


class _FakeTransactionContext:
    def __init__(self, tx: _FakeTransaction) -> None:
        self._tx = tx

    async def __aenter__(self) -> _FakeTransaction:
        return self._tx

    async def __aexit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        return None


class _FakeDatabase:
    def __init__(self) -> None:
        self.connected = False
        self.closed = False
        self.fetch_rows: list[dict[str, object] | None] = []
        self.fetch_results: list[list[dict[str, object]]] = []
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.closed = True

    async def execute(self, query: str, *params: object) -> str:
        self.executed.append((query, params))
        return "INSERT 0 1"

    async def fetch(self, query: str, *params: object) -> list[dict[str, object]]:
        self.executed.append((query, params))
        if self.fetch_results:
            return self.fetch_results.pop(0)
        return []

    async def fetchrow(self, query: str, *params: object) -> dict[str, object] | None:
        self.executed.append((query, params))
        if self.fetch_rows:
            return self.fetch_rows.pop(0)
        return None

    def transaction(self) -> _FakeTransactionContext:
        return _FakeTransactionContext(_FakeTransaction(self))


def _build_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[audit_repositories.AuditRepository, _FakeDatabase]:
    fake_db = _FakeDatabase()

    def _db_factory(_dsn: str) -> _FakeDatabase:
        return fake_db

    monkeypatch.setattr(audit_repositories, "DatabaseClient", _db_factory)
    repository = audit_repositories.AuditRepository(DEFAULT_POSTGRES_DSN)
    return repository, fake_db


def _sample_event() -> EventEnvelope:
    return EventEnvelope(
        event_name="credit.decision.made.v1",
        event_id="event-12345678",
        trace_id="trace-12345678",
        correlation_id="corr-12345678",
        causation_id="cause-12345678",
        producer="decision-service",
        payload={"email": "analyst@example.com", "decision": "approve"},
    )


def _sample_record() -> dict[str, object]:
    return {
        "event_id": "event-12345678",
        "event_name": "credit.decision.made.v1",
        "payload": {"email": "analyst@example.com", "decision": "approve"},
        "trace_id": "trace-12345678",
        "correlation_id": "corr-12345678",
        "causation_id": "cause-12345678",
        "created_at": datetime.now(UTC),
    }


@pytest.mark.unit
def test_audit_repository_connect_and_close(monkeypatch: pytest.MonkeyPatch) -> None:
    repository, fake_db = _build_repository(monkeypatch)

    asyncio.run(repository.connect())
    asyncio.run(repository.close())

    assert fake_db.connected is True
    assert fake_db.closed is True


@pytest.mark.unit
def test_audit_repository_handle_event_inserts_redacted_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, fake_db = _build_repository(monkeypatch)

    async def _record_inbox_first_seen(*_: object) -> bool:
        return True

    monkeypatch.setattr(audit_repositories, "record_inbox_event", _record_inbox_first_seen)

    handled = asyncio.run(repository.handle_event(_sample_event()))

    assert handled is True
    assert any("INSERT INTO audit_events" in query for query, _ in fake_db.executed)
    insert_queries = [
        params for query, params in fake_db.executed if "INSERT INTO audit_events" in query
    ]
    assert insert_queries, "expected audit event insert query"
    serialized_payload = str(insert_queries[-1][2])
    assert "***REDACTED***" in serialized_payload


@pytest.mark.unit
def test_audit_repository_handle_event_skips_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, fake_db = _build_repository(monkeypatch)

    async def _record_inbox_duplicate(*_: object) -> bool:
        return False

    monkeypatch.setattr(audit_repositories, "record_inbox_event", _record_inbox_duplicate)

    handled = asyncio.run(repository.handle_event(_sample_event()))

    assert handled is False
    assert not any("INSERT INTO audit_events" in query for query, _ in fake_db.executed)


@pytest.mark.unit
def test_audit_repository_list_events_returns_typed_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, fake_db = _build_repository(monkeypatch)
    fake_db.fetch_results.append([_sample_record()])

    records = asyncio.run(
        repository.list_events(
            event_name="credit.decision.made.v1",
            trace_id="trace-12345678",
            correlation_id="corr-12345678",
            limit=10,
        )
    )

    assert len(records) == 1
    assert records[0].event_id == "event-12345678"
    assert records[0].correlation_id == "corr-12345678"
    assert records[0].causation_id == "cause-12345678"
    assert records[0].payload["email"] == "***REDACTED***"


@pytest.mark.unit
def test_audit_repository_get_event_returns_none_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, _ = _build_repository(monkeypatch)

    result = asyncio.run(repository.get_event("event-missing"))

    assert result is None


@pytest.mark.unit
def test_audit_repository_get_event_returns_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, fake_db = _build_repository(monkeypatch)
    fake_db.fetch_rows.append(_sample_record())

    result = asyncio.run(repository.get_event("event-12345678"))

    assert result is not None
    assert result.event_name == "credit.decision.made.v1"


@pytest.mark.unit
def test_audit_repository_list_events_rejects_invalid_json_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, fake_db = _build_repository(monkeypatch)
    invalid_payload_record = _sample_record()
    invalid_payload_record["payload"] = "{invalid-json"
    fake_db.fetch_results.append([invalid_payload_record])

    with pytest.raises(ServiceError) as error:
        asyncio.run(
            repository.list_events(
                event_name=None,
                trace_id=None,
                correlation_id=None,
                limit=1,
            )
        )

    assert error.value.error_code == "AUDIT_PAYLOAD_INVALID_JSON"


@pytest.mark.unit
def test_audit_repository_list_events_rejects_invalid_payload_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, fake_db = _build_repository(monkeypatch)
    invalid_payload_record = _sample_record()
    invalid_payload_record["payload"] = [1, 2, 3]
    fake_db.fetch_results.append([invalid_payload_record])

    with pytest.raises(ServiceError) as error:
        asyncio.run(
            repository.list_events(
                event_name=None,
                trace_id=None,
                correlation_id=None,
                limit=1,
            )
        )

    assert error.value.error_code == "AUDIT_PAYLOAD_INVALID_TYPE"


@pytest.mark.unit
def test_audit_repository_get_event_rejects_invalid_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, fake_db = _build_repository(monkeypatch)
    invalid_row = _sample_record()
    invalid_row["created_at"] = "not-a-datetime"
    fake_db.fetch_rows.append(invalid_row)

    with pytest.raises(ServiceError) as error:
        asyncio.run(repository.get_event("event-12345678"))

    assert error.value.error_code == "AUDIT_EVENT_INVALID_TIMESTAMP"


@pytest.mark.unit
def test_audit_repository_get_event_rejects_invalid_trace_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, fake_db = _build_repository(monkeypatch)
    invalid_row = _sample_record()
    invalid_row["trace_id"] = 12345
    fake_db.fetch_rows.append(invalid_row)

    with pytest.raises(ServiceError) as error:
        asyncio.run(repository.get_event("event-12345678"))

    assert error.value.error_code == "AUDIT_EVENT_INVALID_TRACE"


@pytest.mark.unit
def test_audit_repository_get_event_rejects_invalid_correlation_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, fake_db = _build_repository(monkeypatch)
    invalid_row = _sample_record()
    invalid_row["correlation_id"] = 12345
    fake_db.fetch_rows.append(invalid_row)

    with pytest.raises(ServiceError) as error:
        asyncio.run(repository.get_event("event-12345678"))

    assert error.value.error_code == "AUDIT_EVENT_INVALID_CORRELATION"


@pytest.mark.unit
def test_audit_repository_get_event_rejects_invalid_event_id_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, fake_db = _build_repository(monkeypatch)
    invalid_row = _sample_record()
    invalid_row["event_id"] = 999
    fake_db.fetch_rows.append(invalid_row)

    with pytest.raises(ServiceError) as error:
        asyncio.run(repository.get_event("event-12345678"))

    assert error.value.error_code == "AUDIT_EVENT_INVALID_VALUE"
