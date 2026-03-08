from __future__ import annotations

from datetime import UTC, datetime

import observability_audit.routes as audit_routes
import pytest
from fastapi.testclient import TestClient
from observability_audit.main import app
from observability_audit.repositories import AuditEventRecord

from contracts import EventEnvelope
from shared_kernel import ServiceSettings
from tests.defaults import DEFAULT_POSTGRES_DSN, DEFAULT_RABBITMQ_URL


class _FakeAuditRepository:
    def __init__(self) -> None:
        self.connected = False
        self.closed = False
        self.last_written_event_payload: dict[str, object] | None = None
        self.events: list[AuditEventRecord] = [
            AuditEventRecord(
                event_id="event-00000001",
                event_name="credit.decision.made.v1",
                payload={"email": "***REDACTED***", "decision": "approve"},
                trace_id="trace-00000001",
                correlation_id="corr-00000001",
                causation_id=None,
                created_at=datetime.now(UTC),
            ),
            AuditEventRecord(
                event_id="event-00000002",
                event_name="credit.assistant.summarized.v1",
                payload={"summary": "ok"},
                trace_id="trace-00000001",
                correlation_id="corr-00000001",
                causation_id="event-00000001",
                created_at=datetime.now(UTC),
            ),
        ]

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.closed = True

    async def handle_event(self, event: EventEnvelope) -> bool:
        self.last_written_event_payload = event.payload
        return True

    async def list_events(
        self,
        *,
        event_name: str | None,
        trace_id: str | None,
        correlation_id: str | None,
        limit: int,
    ) -> list[AuditEventRecord]:
        filtered = self.events
        if event_name is not None:
            filtered = [event for event in filtered if event.event_name == event_name]
        if trace_id is not None:
            filtered = [event for event in filtered if event.trace_id == trace_id]
        if correlation_id is not None:
            filtered = [event for event in filtered if event.correlation_id == correlation_id]
        return filtered[:limit]

    async def get_event(self, event_id: str) -> AuditEventRecord | None:
        for event in self.events:
            if event.event_id == event_id:
                return event
        return None


def _settings(_: str) -> ServiceSettings:
    return ServiceSettings(
        service_name="observability-audit",
        postgres_dsn=DEFAULT_POSTGRES_DSN,
        rabbitmq_url=DEFAULT_RABBITMQ_URL,
    )


def _install_fake_repository(
    monkeypatch: pytest.MonkeyPatch,
    repository: _FakeAuditRepository,
) -> None:
    def _repository_factory(postgres_dsn: str) -> _FakeAuditRepository:
        _ = postgres_dsn
        return repository

    monkeypatch.setattr(audit_routes, "AuditRepository", _repository_factory)
    monkeypatch.setattr(audit_routes, "load_settings", _settings)


@pytest.mark.unit
def test_write_event_redacts_pii_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_repository = _FakeAuditRepository()
    _install_fake_repository(monkeypatch, fake_repository)
    client = TestClient(app)

    response = client.post(
        "/v1/audit/events",
        json={
            "event_name": "credit.decision.made.v1",
            "payload": {"email": "user@example.com", "decision": "approve"},
        },
        headers={"x-idempotency-key": "audit-idem-0001"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "accepted"
    assert payload["payload"]["email"] == "***REDACTED***"
    assert fake_repository.last_written_event_payload is not None
    assert fake_repository.last_written_event_payload["email"] == "***REDACTED***"
    assert response.headers["x-correlation-id"] == response.headers["x-trace-id"]


@pytest.mark.unit
def test_list_events_supports_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_repository = _FakeAuditRepository()
    _install_fake_repository(monkeypatch, fake_repository)
    client = TestClient(app)

    response = client.get(
        "/v1/audit/events",
        params={"trace_id": "trace-00000001", "limit": 10},
        headers={"x-idempotency-key": "audit-idem-0002"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    assert len(payload["events"]) == 2
    assert all(event["correlation_id"] == "corr-00000001" for event in payload["events"])


@pytest.mark.unit
def test_get_event_returns_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_repository = _FakeAuditRepository()
    _install_fake_repository(monkeypatch, fake_repository)
    client = TestClient(app)

    response = client.get(
        "/v1/audit/events/event-missing",
        headers={"x-idempotency-key": "audit-idem-0003"},
    )

    assert response.status_code == 404
    assert response.json()["error_code"] == "AUDIT_EVENT_NOT_FOUND"


@pytest.mark.unit
def test_list_trace_events_returns_trace_scoped_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_repository = _FakeAuditRepository()
    _install_fake_repository(monkeypatch, fake_repository)
    client = TestClient(app)

    response = client.get(
        "/v1/audit/traces/trace-00000001",
        headers={"x-idempotency-key": "audit-idem-0004"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    assert all(event["trace_id"] == "trace-00000001" for event in payload["events"])


@pytest.mark.unit
def test_list_events_supports_correlation_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_repository = _FakeAuditRepository()
    _install_fake_repository(monkeypatch, fake_repository)
    client = TestClient(app)

    response = client.get(
        "/v1/audit/events",
        params={"correlation_id": "corr-00000001", "limit": 10},
        headers={"x-idempotency-key": "audit-idem-0007"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    assert all(event["correlation_id"] == "corr-00000001" for event in payload["events"])


@pytest.mark.unit
def test_audit_status_rejects_short_idempotency_key() -> None:
    client = TestClient(app)

    response = client.get(
        "/v1/audit/status",
        headers={"x-idempotency-key": "short"},
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "INVALID_IDEMPOTENCY_KEY"


@pytest.mark.unit
def test_write_event_rejects_invalid_event_name(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_repository = _FakeAuditRepository()
    _install_fake_repository(monkeypatch, fake_repository)
    client = TestClient(app)

    response = client.post(
        "/v1/audit/events",
        json={
            "event_name": "invalid-event-name",
            "payload": {"decision": "approve"},
        },
        headers={"x-idempotency-key": "audit-idem-0005"},
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "INVALID_EVENT_NAME"


@pytest.mark.unit
def test_list_events_rejects_invalid_event_name_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_repository = _FakeAuditRepository()
    _install_fake_repository(monkeypatch, fake_repository)
    client = TestClient(app)

    response = client.get(
        "/v1/audit/events",
        params={"event_name": "not-valid"},
        headers={"x-idempotency-key": "audit-idem-0006"},
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "INVALID_EVENT_NAME"
