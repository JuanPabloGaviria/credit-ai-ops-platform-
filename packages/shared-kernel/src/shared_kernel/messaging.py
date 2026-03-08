"""RabbitMQ transport primitives with exchange, queue, and DLQ setup."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar, cast

import aio_pika
from aio_pika.abc import (
    AbstractChannel,
    AbstractConnection,
    AbstractExchange,
    AbstractIncomingMessage,
    AbstractQueue,
)
from opentelemetry.trace import SpanKind, Status, StatusCode
from pydantic import ValidationError

from contracts import (
    EXCHANGE_CREDIT_EVENTS,
    EXCHANGE_CREDIT_EVENTS_DLX,
    EventEnvelope,
)
from observability import MetricsRegistry

from .config import ServiceSettings
from .errors import ServiceError
from .resilience import Bulkhead, CircuitBreaker, with_retries, with_timeout
from .telemetry import extract_trace_context, get_tracer, inject_trace_context
from .tracing import event_observability_context, observability_context

EventHandler = Callable[[EventEnvelope], Awaitable[bool | None]]
T = TypeVar("T")
_NO_CIRCUIT_FAILURE_CODES = frozenset({"CIRCUIT_OPEN", "BULKHEAD_REJECTED"})


class RabbitMQClient:
    """RabbitMQ helper encapsulating publish/consume and DLQ topology."""

    def __init__(
        self,
        url: str,
        *,
        exchange_name: str = EXCHANGE_CREDIT_EVENTS,
        dead_letter_exchange: str = EXCHANGE_CREDIT_EVENTS_DLX,
        request_timeout_seconds: float = 3.0,
        retry_max_attempts: int = 3,
        retry_base_delay_seconds: float = 0.1,
        retry_max_delay_seconds: float = 5.0,
        retry_jitter_seconds: float = 0.2,
        circuit_failure_threshold: int = 5,
        circuit_success_threshold: int = 2,
        circuit_recovery_timeout_seconds: float = 15.0,
        bulkhead_max_concurrency: int = 10,
        prefetch_count: int = 10,
        service_name: str = "rabbitmq",
    ) -> None:
        self._service_name = service_name
        self._url = url
        self._exchange_name = exchange_name
        self._dead_letter_exchange = dead_letter_exchange
        self._request_timeout_seconds = request_timeout_seconds
        self._retry_max_attempts = retry_max_attempts
        self._retry_base_delay_seconds = retry_base_delay_seconds
        self._retry_max_delay_seconds = retry_max_delay_seconds
        self._retry_jitter_seconds = retry_jitter_seconds
        self._prefetch_count = prefetch_count
        self._publish_circuit = CircuitBreaker(
            failure_threshold=circuit_failure_threshold,
            success_threshold=circuit_success_threshold,
            recovery_timeout_seconds=circuit_recovery_timeout_seconds,
        )
        self._consume_circuit = CircuitBreaker(
            failure_threshold=circuit_failure_threshold,
            success_threshold=circuit_success_threshold,
            recovery_timeout_seconds=circuit_recovery_timeout_seconds,
        )
        self._publish_bulkhead = Bulkhead(max_concurrency=bulkhead_max_concurrency)
        self._consume_bulkhead = Bulkhead(max_concurrency=bulkhead_max_concurrency)
        self._metrics = MetricsRegistry(service_name)
        self._tracer = get_tracer(f"{service_name}.rabbitmq")
        self._connection: AbstractConnection | None = None
        self._channel: AbstractChannel | None = None
        self._exchange: AbstractExchange | None = None
        self._dlx: AbstractExchange | None = None
        self._consumer_registrations: list[tuple[AbstractQueue, str]] = []
        self._inflight_handlers: set[asyncio.Task[None]] = set()
        self._shutting_down = False

    async def connect(self) -> None:
        if self._connection is not None:
            return
        self._shutting_down = False
        try:
            self._connection = await aio_pika.connect_robust(self._url)
            self._channel = await self._connection.channel(publisher_confirms=True)
            await self._channel.set_qos(prefetch_count=self._prefetch_count)
            self._exchange = await self._channel.declare_exchange(
                self._exchange_name,
                aio_pika.ExchangeType.TOPIC,
                durable=True,
            )
            self._dlx = await self._channel.declare_exchange(
                self._dead_letter_exchange,
                aio_pika.ExchangeType.TOPIC,
                durable=True,
            )
        except Exception as exc:
            raise ServiceError(
                error_code="BROKER_CONNECT_FAILED",
                message="Failed to connect to RabbitMQ",
                operation="rabbitmq_connect",
                status_code=503,
                cause=str(exc),
                hint="Validate RABBITMQ_URL and broker availability",
            ) from exc

    async def close(self) -> None:
        self._shutting_down = True

        if self._consumer_registrations:
            _ = await asyncio.gather(
                *(
                    queue.cancel(consumer_tag)
                    for queue, consumer_tag in self._consumer_registrations
                ),
                return_exceptions=True,
            )
        self._consumer_registrations.clear()

        if self._inflight_handlers:
            _, pending = await asyncio.wait(
                self._inflight_handlers,
                timeout=self._request_timeout_seconds,
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            self._inflight_handlers.clear()

        if self._channel is not None:
            await self._channel.close()
            self._channel = None
        if self._connection is not None:
            await self._connection.close()
            self._connection = None
        self._exchange = None
        self._dlx = None
        self._shutting_down = False

    async def ensure_queue(self, queue_name: str, routing_keys: list[str]) -> AbstractQueue:
        channel = self._require_channel()
        exchange = self._require_exchange()

        queue = await channel.declare_queue(
            queue_name,
            durable=True,
            arguments={
                "x-dead-letter-exchange": self._dead_letter_exchange,
                "x-dead-letter-routing-key": f"{queue_name}.dlq",
            },
        )

        for key in routing_keys:
            await queue.bind(exchange, routing_key=key)

        dlq = await channel.declare_queue(f"{queue_name}.dlq", durable=True)
        await dlq.bind(self._require_dlx(), routing_key=f"{queue_name}.dlq")
        return queue

    async def publish_event(self, event: EventEnvelope) -> None:
        exchange = self._require_exchange()
        with observability_context(
            trace_id=event.trace_id,
            correlation_id=event.correlation_id,
            causation_id=event.causation_id,
        ):
            with self._tracer.start_as_current_span(
                f"rabbitmq publish {event.event_name}",
                kind=SpanKind.PRODUCER,
            ) as span:
                span.set_attribute("messaging.system", "rabbitmq")
                span.set_attribute("messaging.destination.name", self._exchange_name)
                span.set_attribute("messaging.operation", "publish")
                span.set_attribute("messaging.message.id", event.event_id)
                span.set_attribute("messaging.rabbitmq.routing_key", event.event_name)

                payload = event.model_dump(mode="json")
                headers = {
                    "trace_id": event.trace_id,
                    "correlation_id": event.correlation_id,
                    "causation_id": event.causation_id,
                }
                transport_headers = {
                    key: value for key, value in headers.items() if value is not None
                }
                inject_trace_context(transport_headers)
                message = aio_pika.Message(
                    body=json.dumps(payload).encode("utf-8"),
                    content_type="application/json",
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    message_id=event.event_id,
                    timestamp=event.occurred_at,
                    headers=transport_headers,
                )

                async def publish() -> None:
                    await exchange.publish(message, routing_key=event.event_name)

                try:
                    await self._run_integration_call(
                        operation="rabbitmq_publish_event",
                        attemptable=publish,
                        circuit=self._publish_circuit,
                        bulkhead=self._publish_bulkhead,
                    )
                except Exception as exc:
                    span.record_exception(exc)
                    span.set_status(Status(StatusCode.ERROR))
                    raise

    async def consume(
        self,
        queue_name: str,
        routing_keys: list[str],
        handler: EventHandler,
    ) -> None:
        queue = await self.ensure_queue(queue_name, routing_keys)

        async def _on_message(message: AbstractIncomingMessage) -> None:
            current = asyncio.current_task()
            if current is not None:
                self._inflight_handlers.add(current)

            try:
                async with message.process(requeue=False):
                    envelope = self._decode_envelope(
                        message.body,
                        operation=f"rabbitmq_consume_decode_{queue_name}",
                    )
                    message_headers = _message_headers(message)
                    trace_context = extract_trace_context(message_headers)

                    with self._tracer.start_as_current_span(
                        f"rabbitmq consume {queue_name}",
                        context=trace_context,
                        kind=SpanKind.CONSUMER,
                    ) as span:
                        span.set_attribute("messaging.system", "rabbitmq")
                        span.set_attribute("messaging.destination.name", queue_name)
                        span.set_attribute("messaging.operation", "process")
                        span.set_attribute("messaging.message.id", envelope.event_id)
                        span.set_attribute(
                            "messaging.rabbitmq.routing_key",
                            envelope.event_name,
                        )
                        with event_observability_context(envelope):
                            async def handle() -> bool | None:
                                return await handler(envelope)

                            try:
                                _ = await self._run_integration_call(
                                    operation=f"rabbitmq_consume_{queue_name}",
                                    attemptable=handle,
                                    circuit=self._consume_circuit,
                                    bulkhead=self._consume_bulkhead,
                                )
                            except Exception as exc:
                                span.record_exception(exc)
                                span.set_status(Status(StatusCode.ERROR))
                                raise
            except asyncio.CancelledError:
                if self._shutting_down:
                    return
                raise
            except Exception:
                if self._shutting_down:
                    return
                raise
            finally:
                if current is not None:
                    self._inflight_handlers.discard(current)

        consumer_tag = await queue.consume(_on_message)
        self._consumer_registrations.append((queue, consumer_tag))

    async def replay_dead_letter_queue(self, queue_name: str, limit: int = 100) -> int:
        """Replay events from a queue DLQ back into the primary exchange."""
        channel = self._require_channel()
        dlq_name = f"{queue_name}.dlq"
        dlq = await channel.declare_queue(dlq_name, durable=True)

        replayed = 0
        for _ in range(limit):
            message = await self._run_integration_call(
                operation=f"rabbitmq_dlq_get_{queue_name}",
                attemptable=lambda: dlq.get(fail=False),
                circuit=self._consume_circuit,
                bulkhead=self._consume_bulkhead,
            )
            if message is None:
                break
            async with message.process(requeue=False):
                envelope = self._decode_envelope(
                    message.body,
                    operation=f"rabbitmq_dlq_decode_{queue_name}",
                )
                await self.publish_event(envelope)
                replayed += 1
        return replayed

    def _decode_envelope(self, body: bytes, *, operation: str) -> EventEnvelope:
        try:
            raw_payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ServiceError(
                error_code="BROKER_MESSAGE_INVALID_JSON",
                message="Broker message body is not valid JSON",
                operation=operation,
                status_code=400,
                cause=str(exc),
            ) from exc
        try:
            return EventEnvelope.model_validate(raw_payload)
        except ValidationError as exc:
            raise ServiceError(
                error_code="BROKER_MESSAGE_SCHEMA_INVALID",
                message="Broker message body failed event envelope validation",
                operation=operation,
                status_code=400,
                cause=str(exc),
            ) from exc

    def _require_channel(self) -> AbstractChannel:
        if self._channel is None:
            raise ServiceError(
                error_code="BROKER_CHANNEL_NOT_READY",
                message="RabbitMQ channel is not initialized",
                operation="rabbitmq_require_channel",
                status_code=500,
            )
        return self._channel

    def _require_exchange(self) -> AbstractExchange:
        if self._exchange is None:
            raise ServiceError(
                error_code="BROKER_EXCHANGE_NOT_READY",
                message="RabbitMQ exchange is not initialized",
                operation="rabbitmq_require_exchange",
                status_code=500,
            )
        return self._exchange

    def _require_dlx(self) -> AbstractExchange:
        if self._dlx is None:
            raise ServiceError(
                error_code="BROKER_DLX_NOT_READY",
                message="RabbitMQ dead-letter exchange is not initialized",
                operation="rabbitmq_require_dlx",
                status_code=500,
            )
        return self._dlx

    async def _run_integration_call(
        self,
        *,
        operation: str,
        attemptable: Callable[[], Awaitable[T]],
        circuit: CircuitBreaker,
        bulkhead: Bulkhead,
    ) -> T:
        started = time.perf_counter()
        outcome = "success"
        try:
            circuit.assert_available(operation)

            async def attempt_with_timeout() -> T:
                return await with_timeout(
                    attemptable(),
                    timeout_seconds=self._request_timeout_seconds,
                    operation=operation,
                )

            result = await bulkhead.run(
                operation,
                lambda: with_retries(
                    operation=operation,
                    attemptable=attempt_with_timeout,
                    max_attempts=self._retry_max_attempts,
                    base_delay_seconds=self._retry_base_delay_seconds,
                    max_delay_seconds=self._retry_max_delay_seconds,
                    jitter_seconds=self._retry_jitter_seconds,
                ),
            )
        except ServiceError as exc:
            outcome = "rejected" if exc.error_code in _NO_CIRCUIT_FAILURE_CODES else "failed"
            if exc.error_code not in _NO_CIRCUIT_FAILURE_CODES:
                circuit.record_failure()
            raise
        except Exception as exc:
            outcome = "failed"
            circuit.record_failure()
            raise ServiceError(
                error_code="BROKER_OPERATION_FAILED",
                message="Broker integration operation failed",
                operation=operation,
                status_code=503,
                cause=str(exc),
            ) from exc

        finally:
            self._metrics.observe_integration_call(
                dependency="rabbitmq",
                operation=operation,
                outcome=outcome,
                latency_seconds=time.perf_counter() - started,
            )

        circuit.record_success()
        return result


def build_rabbitmq_client(settings: ServiceSettings) -> RabbitMQClient:
    """Build RabbitMQ client with resilience policy from service settings."""
    return RabbitMQClient(
        url=settings.rabbitmq_url,
        request_timeout_seconds=settings.request_timeout_seconds,
        retry_max_attempts=settings.retry_max_attempts,
        retry_base_delay_seconds=settings.retry_base_delay_seconds,
        retry_max_delay_seconds=settings.retry_max_delay_seconds,
        retry_jitter_seconds=settings.retry_jitter_seconds,
        circuit_failure_threshold=settings.broker_circuit_failure_threshold,
        circuit_success_threshold=settings.broker_circuit_success_threshold,
        circuit_recovery_timeout_seconds=settings.broker_circuit_recovery_timeout_seconds,
        bulkhead_max_concurrency=settings.broker_bulkhead_max_concurrency,
        prefetch_count=settings.broker_prefetch_count,
        service_name=settings.service_name,
    )


def _message_headers(message: AbstractIncomingMessage) -> dict[str, object]:
    raw_headers_obj: object = getattr(message, "headers", {})
    if isinstance(raw_headers_obj, dict):
        raw_headers = cast(dict[object, object], raw_headers_obj)
        return {str(key): value for key, value in raw_headers.items()}
    return {}
