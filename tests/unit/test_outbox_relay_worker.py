from __future__ import annotations

import asyncio
from typing import cast

import pytest

import shared_kernel.outbox_relay as outbox_relay
from contracts import EventEnvelope
from shared_kernel import (
    ClaimedOutboxEvent,
    DatabaseExecutor,
    OutboxRelayConfig,
    OutboxRelayWorker,
    ServiceError,
)


class _FakeDb:
    pass


class _FakePublisher:
    def __init__(self, fail_event_id: str | None = None) -> None:
        self.published_event_ids: list[str] = []
        self._fail_event_id = fail_event_id

    async def publish_event(self, event: EventEnvelope) -> None:
        if self._fail_event_id is not None and event.event_id == self._fail_event_id:
            raise RuntimeError("simulated broker failure")
        self.published_event_ids.append(event.event_id)


def _claimed_event(event_id: str) -> ClaimedOutboxEvent:
    return ClaimedOutboxEvent(
        event=EventEnvelope(
            event_name="credit.application.submitted.v1",
            event_id=event_id,
            trace_id="trace-12345678",
            producer="test-suite",
            payload={"application_id": "app-12345678"},
        ),
        claim_token=f"claim-{event_id}",
        publish_attempts=1,
    )


def _config() -> OutboxRelayConfig:
    return OutboxRelayConfig(
        outbox_table="application_outbox",
        operation_prefix="application_outbox_relay",
        batch_size=10,
        poll_interval_seconds=0.5,
        claim_lease_seconds=30,
        max_publish_attempts=5,
    )


@pytest.mark.unit
def test_outbox_relay_worker_publishes_and_marks_batch(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    publisher = _FakePublisher()
    marked: list[tuple[str, str]] = []

    async def fake_fetch(
        _db: object,
        _table_name: str,
        limit: int = 100,
        *,
        lease_seconds: int = 30,
    ) -> list[ClaimedOutboxEvent]:
        assert limit == 10
        assert lease_seconds == 30
        return [_claimed_event("event-0001"), _claimed_event("event-0002")]

    async def fake_mark_published(
        _db: object,
        _table_name: str,
        event_id: str,
        *,
        claim_token: str,
    ) -> None:
        marked.append((event_id, claim_token))

    monkeypatch.setattr(outbox_relay, "fetch_pending_outbox_events", fake_fetch)
    monkeypatch.setattr(outbox_relay, "mark_outbox_event_published", fake_mark_published)

    worker = OutboxRelayWorker(
        db=cast(DatabaseExecutor, db),
        publish_event=publisher.publish_event,
        config=_config(),
    )

    published = asyncio.run(worker.relay_once())

    assert published == 2
    assert publisher.published_event_ids == ["event-0001", "event-0002"]
    assert marked == [
        ("event-0001", "claim-event-0001"),
        ("event-0002", "claim-event-0002"),
    ]


@pytest.mark.unit
def test_outbox_relay_worker_wraps_broker_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _FakeDb()
    publisher = _FakePublisher(fail_event_id="event-bad-001")
    failed: list[tuple[str, str, int]] = []

    async def fake_fetch(
        _db: object,
        _table_name: str,
        limit: int = 100,
        *,
        lease_seconds: int = 30,
    ) -> list[ClaimedOutboxEvent]:
        _ = limit
        _ = lease_seconds
        return [_claimed_event("event-bad-001")]

    async def fake_mark_failed(
        _db: object,
        _table_name: str,
        event_id: str,
        *,
        claim_token: str,
        error_message: str,
        max_attempts: int,
    ) -> None:
        _ = claim_token
        failed.append((event_id, error_message, max_attempts))

    monkeypatch.setattr(outbox_relay, "fetch_pending_outbox_events", fake_fetch)
    monkeypatch.setattr(outbox_relay, "mark_outbox_event_failed", fake_mark_failed)

    worker = OutboxRelayWorker(
        db=cast(DatabaseExecutor, db),
        publish_event=publisher.publish_event,
        config=_config(),
    )

    with pytest.raises(ServiceError) as error:
        asyncio.run(worker.relay_once())

    assert error.value.error_code == "OUTBOX_RELAY_PUBLISH_FAILED"
    assert failed == [("event-bad-001", "simulated broker failure", 5)]
