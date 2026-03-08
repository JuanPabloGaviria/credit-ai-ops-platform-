import asyncio
import json
import os
from collections.abc import Sequence
from typing import Any, cast

import pytest

from contracts import EventEnvelope
from shared_kernel import (
    ClaimedOutboxEvent,
    DatabaseClient,
    ServiceError,
    enqueue_outbox_event,
    fetch_pending_outbox_events,
)
from tests.defaults import DEFAULT_POSTGRES_DSN


class _FakeDb:
    def __init__(self, rows: Sequence[dict[str, object]]) -> None:
        self._rows = rows

    async def fetch(self, _query: str, *_args: object) -> Sequence[dict[str, object]]:
        return self._rows


@pytest.mark.unit
def test_outbox_rejects_unknown_table_name() -> None:
    event = EventEnvelope(
        event_name="credit.application.submitted.v1",
        event_id="event-12345",
        trace_id="trace-12345",
        producer="test-suite",
        payload={"application_id": "app-12345"},
    )

    with pytest.raises(ServiceError) as error:
        db = DatabaseClient(os.getenv("POSTGRES_DSN", DEFAULT_POSTGRES_DSN))
        asyncio.run(enqueue_outbox_event(db, "invalid_table", event))

    assert error.value.error_code == "UNSUPPORTED_OUTBOX_TABLE"


@pytest.mark.unit
def test_fetch_pending_outbox_events_decodes_json_string_payload() -> None:
    payload: dict[str, Any] = {
        "event_name": "credit.application.submitted.v1",
        "event_id": "event-12345",
        "trace_id": "trace-12345",
        "producer": "test-suite",
        "occurred_at": "2026-03-06T00:00:00Z",
        "payload": {"application_id": "app-12345"},
    }
    db = cast(
        DatabaseClient,
        _FakeDb(
            [{"payload": json.dumps(payload), "claim_token": "claim-123", "publish_attempts": 1}]
        ),
    )

    events = asyncio.run(fetch_pending_outbox_events(db, "application_outbox"))

    assert len(events) == 1
    assert isinstance(events[0], ClaimedOutboxEvent)
    assert events[0].event.event_id == "event-12345"
    assert events[0].event.payload["application_id"] == "app-12345"


@pytest.mark.unit
def test_fetch_pending_outbox_events_rejects_invalid_json() -> None:
    db = cast(
        DatabaseClient,
        _FakeDb([{"payload": "{invalid-json", "claim_token": "claim-123", "publish_attempts": 1}]),
    )

    with pytest.raises(ServiceError) as error:
        asyncio.run(fetch_pending_outbox_events(db, "application_outbox"))

    assert error.value.error_code == "OUTBOX_PAYLOAD_INVALID_JSON"
