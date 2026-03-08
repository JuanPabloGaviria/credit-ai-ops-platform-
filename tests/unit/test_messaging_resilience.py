import asyncio
from datetime import UTC, datetime
from typing import cast

import pytest
from aio_pika.abc import AbstractExchange

from contracts import EventEnvelope
from shared_kernel import RabbitMQClient, ServiceError


class _FlakyExchange:
    def __init__(self, failures_before_success: int) -> None:
        self._failures_before_success = failures_before_success
        self.publish_attempts = 0

    async def publish(self, message: object, routing_key: str) -> None:
        _ = message
        _ = routing_key
        self.publish_attempts += 1
        if self.publish_attempts <= self._failures_before_success:
            raise RuntimeError("transient_broker_failure")


class _TestRabbitMQClient(RabbitMQClient):
    def bind_exchange_for_test(self, exchange: AbstractExchange) -> None:
        self._exchange = exchange


def _build_event() -> EventEnvelope:
    return EventEnvelope(
        event_name="credit.application.submitted.v1",
        event_id="event-0001",
        trace_id="trace-0001",
        producer="unit-test",
        occurred_at=datetime.now(UTC),
        payload={"application_id": "app-0001"},
    )


@pytest.mark.unit
def test_publish_event_retries_then_succeeds() -> None:
    async def scenario() -> None:
        client = _TestRabbitMQClient(
            url="amqp://unused",
            request_timeout_seconds=1.0,
            retry_max_attempts=3,
            retry_base_delay_seconds=0.01,
            retry_max_delay_seconds=0.03,
            retry_jitter_seconds=0.0,
            circuit_failure_threshold=3,
            circuit_success_threshold=1,
            bulkhead_max_concurrency=1,
            prefetch_count=1,
        )
        exchange = _FlakyExchange(failures_before_success=2)
        client.bind_exchange_for_test(cast(AbstractExchange, exchange))

        await client.publish_event(_build_event())

        assert exchange.publish_attempts == 3

    asyncio.run(scenario())


@pytest.mark.unit
def test_publish_event_opens_circuit_after_repeated_failures() -> None:
    async def scenario() -> None:
        client = _TestRabbitMQClient(
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
        exchange = _FlakyExchange(failures_before_success=10)
        client.bind_exchange_for_test(cast(AbstractExchange, exchange))

        with pytest.raises(ServiceError):
            await client.publish_event(_build_event())
        with pytest.raises(ServiceError):
            await client.publish_event(_build_event())

        with pytest.raises(ServiceError) as error:
            await client.publish_event(_build_event())

        assert error.value.error_code == "CIRCUIT_OPEN"
        assert exchange.publish_attempts == 2

    asyncio.run(scenario())
