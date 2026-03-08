import asyncio
import json
from datetime import UTC, datetime
from typing import cast

import pytest
from aio_pika.abc import AbstractChannel, AbstractExchange

from contracts import EventEnvelope
from shared_kernel import RabbitMQClient, ServiceError


class _FakeMessageContext:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(
        self,
        _exc_type: object,
        _exc: object,
        _tb: object,
    ) -> None:
        return None


class _FakeIncomingMessage:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def process(self, *, requeue: bool = False) -> _FakeMessageContext:
        _ = requeue
        return _FakeMessageContext()


class _FakeQueue:
    def __init__(self, messages: list[_FakeIncomingMessage]) -> None:
        self._messages = messages

    async def get(self, *, fail: bool = False) -> _FakeIncomingMessage | None:
        _ = fail
        if not self._messages:
            return None
        return self._messages.pop(0)


class _FakeChannel:
    def __init__(self, queue: _FakeQueue) -> None:
        self._queue = queue

    async def declare_queue(self, _name: str, *, durable: bool = True) -> _FakeQueue:
        _ = durable
        return self._queue


class _FakeExchange:
    def __init__(self) -> None:
        self.publish_attempts = 0
        self.routing_keys: list[str] = []

    async def publish(self, message: object, routing_key: str) -> None:
        _ = message
        self.publish_attempts += 1
        self.routing_keys.append(routing_key)


class _ReplayTestRabbitMQClient(RabbitMQClient):
    def bind_replay_components_for_test(
        self,
        channel: AbstractChannel,
        exchange: AbstractExchange,
    ) -> None:
        self._channel = channel
        self._exchange = exchange


def _event_payload() -> dict[str, object]:
    event = EventEnvelope(
        event_name="credit.application.submitted.v1",
        event_id="event-0001",
        trace_id="trace-0001",
        producer="unit-test",
        occurred_at=datetime.now(UTC),
        payload={"application_id": "app-0001"},
    )
    return event.model_dump(mode="json")


@pytest.mark.unit
def test_replay_dead_letter_queue_republishes_valid_events() -> None:
    async def scenario() -> None:
        queue = _FakeQueue(
            [
                _FakeIncomingMessage(json.dumps(_event_payload()).encode("utf-8")),
            ]
        )
        channel = cast(AbstractChannel, _FakeChannel(queue))
        exchange = cast(AbstractExchange, _FakeExchange())
        client = _ReplayTestRabbitMQClient(
            url="amqp://unused",
            request_timeout_seconds=1.0,
            retry_max_attempts=1,
            retry_base_delay_seconds=0.01,
            retry_max_delay_seconds=0.02,
            retry_jitter_seconds=0.0,
            circuit_failure_threshold=2,
            circuit_success_threshold=1,
            bulkhead_max_concurrency=1,
            prefetch_count=1,
        )
        client.bind_replay_components_for_test(channel=channel, exchange=exchange)

        replayed = await client.replay_dead_letter_queue("feature.application_submitted", limit=10)

        fake_exchange = cast(_FakeExchange, exchange)
        assert replayed == 1
        assert fake_exchange.publish_attempts == 1
        assert fake_exchange.routing_keys == ["credit.application.submitted.v1"]

    asyncio.run(scenario())


@pytest.mark.unit
def test_replay_dead_letter_queue_rejects_invalid_json() -> None:
    async def scenario() -> None:
        queue = _FakeQueue([_FakeIncomingMessage(b"{invalid-json")])
        channel = cast(AbstractChannel, _FakeChannel(queue))
        exchange = cast(AbstractExchange, _FakeExchange())
        client = _ReplayTestRabbitMQClient(
            url="amqp://unused",
            request_timeout_seconds=1.0,
            retry_max_attempts=1,
            retry_base_delay_seconds=0.01,
            retry_max_delay_seconds=0.02,
            retry_jitter_seconds=0.0,
            circuit_failure_threshold=2,
            circuit_success_threshold=1,
            bulkhead_max_concurrency=1,
            prefetch_count=1,
        )
        client.bind_replay_components_for_test(channel=channel, exchange=exchange)

        with pytest.raises(ServiceError) as error:
            await client.replay_dead_letter_queue("feature.application_submitted", limit=1)

        assert error.value.error_code == "BROKER_MESSAGE_INVALID_JSON"

    asyncio.run(scenario())


@pytest.mark.unit
def test_replay_dead_letter_queue_rejects_invalid_event_envelope() -> None:
    async def scenario() -> None:
        queue = _FakeQueue([_FakeIncomingMessage(b'{"invalid": "payload"}')])
        channel = cast(AbstractChannel, _FakeChannel(queue))
        exchange = cast(AbstractExchange, _FakeExchange())
        client = _ReplayTestRabbitMQClient(
            url="amqp://unused",
            request_timeout_seconds=1.0,
            retry_max_attempts=1,
            retry_base_delay_seconds=0.01,
            retry_max_delay_seconds=0.02,
            retry_jitter_seconds=0.0,
            circuit_failure_threshold=2,
            circuit_success_threshold=1,
            bulkhead_max_concurrency=1,
            prefetch_count=1,
        )
        client.bind_replay_components_for_test(channel=channel, exchange=exchange)

        with pytest.raises(ServiceError) as error:
            await client.replay_dead_letter_queue("feature.application_submitted", limit=1)

        assert error.value.error_code == "BROKER_MESSAGE_SCHEMA_INVALID"

    asyncio.run(scenario())
