from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Protocol, cast

import pytest
from aio_pika.abc import (
    AbstractChannel,
    AbstractConnection,
    AbstractExchange,
    AbstractIncomingMessage,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from contracts import EventEnvelope
from shared_kernel import (
    RabbitMQClient,
    ServiceError,
    ServiceSettings,
    configure_telemetry,
    force_flush_telemetry,
    get_tracer,
    shutdown_telemetry,
)
from shared_kernel.resilience import Bulkhead, CircuitBreaker
from tests.defaults import DEFAULT_POSTGRES_DSN, DEFAULT_RABBITMQ_URL


class _FakeProcessContext:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(
        self,
        _exc_type: object,
        _exc: object,
        _tb: object,
    ) -> None:
        return None


class _MessageWithHeaders(Protocol):
    headers: dict[str, object]


class _FakeIncomingMessage:
    def __init__(self, body: bytes, headers: dict[str, object] | None = None) -> None:
        self.body = body
        self.headers = headers or {}

    def process(self, *, requeue: bool = False) -> _FakeProcessContext:
        _ = requeue
        return _FakeProcessContext()


class _FakeExchange:
    def __init__(self) -> None:
        self.published_routing_keys: list[str] = []
        self.messages: list[object] = []

    async def publish(self, _message: object, routing_key: str) -> None:
        self.messages.append(_message)
        self.published_routing_keys.append(routing_key)


class _FakeQueue:
    def __init__(
        self,
        name: str,
        *,
        fail_cancel: bool = False,
    ) -> None:
        self.name = name
        self.bindings: list[str] = []
        self.consumer_tag = f"{name}-consumer"
        self.cancel_calls: list[str] = []
        self.fail_cancel = fail_cancel
        self._consumer: Callable[[AbstractIncomingMessage], Awaitable[None]] | None = None

    async def bind(self, _exchange: AbstractExchange, *, routing_key: str) -> None:
        self.bindings.append(routing_key)

    async def consume(
        self,
        callback: Callable[[AbstractIncomingMessage], Awaitable[None]],
    ) -> str:
        self._consumer = callback
        return self.consumer_tag

    async def cancel(self, consumer_tag: str) -> None:
        self.cancel_calls.append(consumer_tag)
        if self.fail_cancel:
            raise RuntimeError("queue cancel failed")

    async def deliver(self, body: bytes, headers: dict[str, object] | None = None) -> None:
        if self._consumer is None:
            raise AssertionError("consumer callback was not registered")
        await self._consumer(cast(AbstractIncomingMessage, _FakeIncomingMessage(body, headers)))


class _FakeChannel:
    def __init__(self) -> None:
        self.closed = False
        self.qos_values: list[int] = []
        self.exchanges: dict[str, _FakeExchange] = {}
        self.queues: dict[str, _FakeQueue] = {}
        self.declared_queue_arguments: dict[str, dict[str, object] | None] = {}
        self.fail_cancel_queue_names: set[str] = set()

    async def set_qos(self, *, prefetch_count: int) -> None:
        self.qos_values.append(prefetch_count)

    async def declare_exchange(
        self,
        name: str,
        _exchange_type: object,
        *,
        durable: bool = True,
    ) -> _FakeExchange:
        _ = durable
        exchange = self.exchanges.get(name)
        if exchange is None:
            exchange = _FakeExchange()
            self.exchanges[name] = exchange
        return exchange

    async def declare_queue(
        self,
        name: str,
        *,
        durable: bool = True,
        arguments: dict[str, object] | None = None,
    ) -> _FakeQueue:
        _ = durable
        queue = self.queues.get(name)
        if queue is None:
            queue = _FakeQueue(name, fail_cancel=name in self.fail_cancel_queue_names)
            self.queues[name] = queue
        self.declared_queue_arguments[name] = arguments
        return queue

    async def close(self) -> None:
        self.closed = True


class _FakeConnection:
    def __init__(self, channel: _FakeChannel) -> None:
        self._channel = channel
        self.closed = False
        self.channel_calls = 0

    async def channel(self, *, publisher_confirms: bool = True) -> _FakeChannel:
        _ = publisher_confirms
        self.channel_calls += 1
        return self._channel

    async def close(self) -> None:
        self.closed = True


class _InspectableRabbitMQClient(RabbitMQClient):
    def bind_components_for_test(
        self,
        *,
        connection: AbstractConnection | None = None,
        channel: AbstractChannel | None = None,
        exchange: AbstractExchange | None = None,
        dlx: AbstractExchange | None = None,
    ) -> None:
        self._connection = connection
        self._channel = channel
        self._exchange = exchange
        self._dlx = dlx

    def set_shutting_down_for_test(self, value: bool) -> None:
        self._shutting_down = value

    def add_inflight_task_for_test(self, task: asyncio.Task[None]) -> None:
        self._inflight_handlers.add(task)

    def registration_count_for_test(self) -> int:
        return len(self._consumer_registrations)

    async def run_integration_call_for_test(
        self,
        *,
        operation: str,
        attemptable: Callable[[], Awaitable[int]],
        circuit: CircuitBreaker,
        bulkhead: Bulkhead,
    ) -> int:
        return await self._run_integration_call(
            operation=operation,
            attemptable=attemptable,
            circuit=circuit,
            bulkhead=bulkhead,
        )


def _build_event() -> EventEnvelope:
    return EventEnvelope(
        event_name="credit.application.submitted.v1",
        event_id="event-0001",
        trace_id="trace-0001",
        correlation_id="corr-0001",
        causation_id="cause-0001",
        producer="unit-test",
        occurred_at=datetime.now(UTC),
        payload={"application_id": "app-0001"},
    )


def _build_client() -> _InspectableRabbitMQClient:
    return _InspectableRabbitMQClient(
        url="amqp://mq.example:5672/",
        request_timeout_seconds=0.05,
        retry_max_attempts=1,
        retry_base_delay_seconds=0.01,
        retry_max_delay_seconds=0.02,
        retry_jitter_seconds=0.0,
        circuit_failure_threshold=2,
        circuit_success_threshold=1,
        bulkhead_max_concurrency=2,
        prefetch_count=7,
    )


def _telemetry_settings(service_name: str) -> ServiceSettings:
    return ServiceSettings(
        service_name=service_name,
        postgres_dsn=DEFAULT_POSTGRES_DSN,
        rabbitmq_url=DEFAULT_RABBITMQ_URL,
        otel_enabled=True,
        otel_exporter_otlp_endpoint="http://collector.internal:4318/v1/traces",
    )


@pytest.mark.unit
def test_connect_success_and_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        channel = _FakeChannel()
        connection = _FakeConnection(channel)
        connect_calls = 0

        async def _fake_connect_robust(url: str) -> _FakeConnection:
            nonlocal connect_calls
            connect_calls += 1
            assert url == "amqp://mq.example:5672/"
            return connection

        monkeypatch.setattr(
            "shared_kernel.messaging.aio_pika.connect_robust",
            _fake_connect_robust,
        )
        client = _build_client()

        await client.connect()
        await client.connect()

        assert connect_calls == 1
        assert connection.channel_calls == 1
        assert channel.qos_values == [7]

    asyncio.run(scenario())


@pytest.mark.unit
def test_connect_failure_raises_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        async def _fake_connect_robust(_url: str) -> _FakeConnection:
            raise RuntimeError("broker unavailable")

        monkeypatch.setattr(
            "shared_kernel.messaging.aio_pika.connect_robust",
            _fake_connect_robust,
        )
        client = _build_client()

        with pytest.raises(ServiceError) as error:
            await client.connect()

        assert error.value.error_code == "BROKER_CONNECT_FAILED"

    asyncio.run(scenario())


@pytest.mark.unit
def test_rabbitmq_require_guards_raise_typed_errors() -> None:
    async def scenario() -> None:
        client = _build_client()
        routing_keys = ["credit.application.submitted.v1"]

        with pytest.raises(ServiceError) as channel_error:
            await client.ensure_queue("feature.application_submitted", routing_keys)

        with pytest.raises(ServiceError) as exchange_error:
            await client.publish_event(_build_event())

        fake_channel = _FakeChannel()
        fake_exchange = _FakeExchange()
        client.bind_components_for_test(
            channel=cast(AbstractChannel, fake_channel),
            exchange=cast(AbstractExchange, fake_exchange),
        )
        with pytest.raises(ServiceError) as dlx_error:
            await client.ensure_queue("feature.application_submitted", routing_keys)

        assert channel_error.value.error_code == "BROKER_CHANNEL_NOT_READY"
        assert exchange_error.value.error_code == "BROKER_EXCHANGE_NOT_READY"
        assert dlx_error.value.error_code == "BROKER_DLX_NOT_READY"

    asyncio.run(scenario())


@pytest.mark.unit
def test_consume_registers_consumer_and_dispatches_event() -> None:
    async def scenario() -> None:
        from shared_kernel import get_causation_id, get_correlation_id, get_trace_id

        client = _build_client()
        channel = _FakeChannel()
        exchange = _FakeExchange()
        dlx = _FakeExchange()
        client.bind_components_for_test(
            channel=cast(AbstractChannel, channel),
            exchange=cast(AbstractExchange, exchange),
            dlx=cast(AbstractExchange, dlx),
        )
        handled_context: list[tuple[str, str, str, str | None]] = []

        async def _handler(event: EventEnvelope) -> bool:
            handled_context.append(
                (
                    event.event_id,
                    get_trace_id(),
                    get_correlation_id(),
                    get_causation_id(),
                )
            )
            return True

        await client.consume(
            queue_name="feature.application_submitted",
            routing_keys=["credit.application.submitted.v1"],
            handler=_handler,
        )
        queue = channel.queues["feature.application_submitted"]
        message = json.dumps(_build_event().model_dump(mode="json")).encode("utf-8")
        await queue.deliver(message)

        assert handled_context == [("event-0001", "trace-0001", "corr-0001", "event-0001")]
        assert queue.bindings == ["credit.application.submitted.v1"]
        assert client.registration_count_for_test() == 1
        assert channel.declared_queue_arguments["feature.application_submitted"] == {
            "x-dead-letter-exchange": "credit.events.dlx",
            "x-dead-letter-routing-key": "feature.application_submitted.dlq",
        }

    asyncio.run(scenario())


@pytest.mark.unit
def test_publish_event_sets_observability_headers() -> None:
    async def scenario() -> None:
        client = _build_client()
        exchange = _FakeExchange()
        client.bind_components_for_test(exchange=cast(AbstractExchange, exchange))

        configure_telemetry(
            _telemetry_settings("rabbitmq-test"),
            span_exporter_factory=InMemorySpanExporter,
        )
        try:
            with get_tracer("tests.rabbitmq").start_as_current_span("publish-parent"):
                await client.publish_event(_build_event())
            force_flush_telemetry()
        finally:
            shutdown_telemetry()

        assert exchange.published_routing_keys == ["credit.application.submitted.v1"]
        assert exchange.messages, "published message must be captured"
        message = cast(_MessageWithHeaders, exchange.messages[0])
        headers = message.headers
        assert headers["trace_id"] == "trace-0001"
        assert headers["correlation_id"] == "corr-0001"
        assert headers["causation_id"] == "cause-0001"
        assert str(headers["traceparent"]).startswith("00-")

    asyncio.run(scenario())


@pytest.mark.unit
def test_consume_swallows_decode_errors_during_shutdown() -> None:
    async def scenario() -> None:
        client = _build_client()
        channel = _FakeChannel()
        exchange = _FakeExchange()
        dlx = _FakeExchange()
        client.bind_components_for_test(
            channel=cast(AbstractChannel, channel),
            exchange=cast(AbstractExchange, exchange),
            dlx=cast(AbstractExchange, dlx),
        )
        handled_calls = 0

        async def _handler(_event: EventEnvelope) -> bool:
            nonlocal handled_calls
            handled_calls += 1
            return True

        await client.consume(
            queue_name="feature.application_submitted",
            routing_keys=["credit.application.submitted.v1"],
            handler=_handler,
        )
        client.set_shutting_down_for_test(True)

        queue = channel.queues["feature.application_submitted"]
        await queue.deliver(b"{invalid-json")

        assert handled_calls == 0

    asyncio.run(scenario())


@pytest.mark.unit
def test_close_cancels_consumers_and_inflight_tasks() -> None:
    async def scenario() -> None:
        client = _build_client()
        channel = _FakeChannel()
        exchange = _FakeExchange()
        dlx = _FakeExchange()
        connection = _FakeConnection(channel)
        client.bind_components_for_test(
            connection=cast(AbstractConnection, connection),
            channel=cast(AbstractChannel, channel),
            exchange=cast(AbstractExchange, exchange),
            dlx=cast(AbstractExchange, dlx),
        )

        async def _handler(_event: EventEnvelope) -> bool:
            return True

        await client.consume(
            queue_name="feature.application_submitted",
            routing_keys=["credit.application.submitted.v1"],
            handler=_handler,
        )
        queue = channel.queues["feature.application_submitted"]

        cancelled = asyncio.Event()

        async def _inflight() -> None:
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        client.add_inflight_task_for_test(asyncio.create_task(_inflight()))
        await client.close()

        assert queue.cancel_calls == [queue.consumer_tag]
        assert cancelled.is_set() is True
        assert channel.closed is True
        assert connection.closed is True
        assert client.registration_count_for_test() == 0

    asyncio.run(scenario())


@pytest.mark.unit
def test_close_swallow_consumer_cancel_errors() -> None:
    async def scenario() -> None:
        client = _build_client()
        channel = _FakeChannel()
        channel.fail_cancel_queue_names.add("feature.application_submitted")
        exchange = _FakeExchange()
        dlx = _FakeExchange()
        connection = _FakeConnection(channel)
        client.bind_components_for_test(
            connection=cast(AbstractConnection, connection),
            channel=cast(AbstractChannel, channel),
            exchange=cast(AbstractExchange, exchange),
            dlx=cast(AbstractExchange, dlx),
        )

        async def _handler(_event: EventEnvelope) -> bool:
            return True

        await client.consume(
            queue_name="feature.application_submitted",
            routing_keys=["credit.application.submitted.v1"],
            handler=_handler,
        )
        await client.close()

        assert channel.closed is True
        assert connection.closed is True

    asyncio.run(scenario())


@pytest.mark.unit
def test_run_integration_call_does_not_count_circuit_open_as_failure() -> None:
    async def scenario() -> None:
        client = _build_client()
        circuit = CircuitBreaker(failure_threshold=1, success_threshold=1)
        bulkhead = Bulkhead(max_concurrency=1)

        async def _attempt() -> int:
            raise ServiceError(
                error_code="CIRCUIT_OPEN",
                message="open",
                operation="test_operation",
                status_code=503,
            )

        with pytest.raises(ServiceError) as error:
            await client.run_integration_call_for_test(
                operation="test_operation",
                attemptable=_attempt,
                circuit=circuit,
                bulkhead=bulkhead,
            )

        assert error.value.error_code == "CIRCUIT_OPEN"
        circuit.assert_available("post_check")

    asyncio.run(scenario())
