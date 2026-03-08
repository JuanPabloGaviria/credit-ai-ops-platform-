"""Request and event context propagation primitives."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4

from fastapi import Request
from opentelemetry.trace import SpanKind, Status, StatusCode
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from contracts import EventEnvelope

from .telemetry import extract_trace_context, get_tracer, inject_trace_context

TRACE_ID_HEADER = "x-trace-id"
CORRELATION_ID_HEADER = "x-correlation-id"
CAUSATION_ID_HEADER = "x-causation-id"
TRACEPARENT_HEADER = "traceparent"
trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="")
correlation_id_ctx: ContextVar[str] = ContextVar("correlation_id", default="")
causation_id_ctx: ContextVar[str] = ContextVar("causation_id", default="")


def get_trace_id() -> str:
    trace_id = trace_id_ctx.get()
    if trace_id:
        return trace_id
    generated = str(uuid4())
    trace_id_ctx.set(generated)
    return generated


def get_correlation_id() -> str:
    correlation_id = correlation_id_ctx.get()
    if correlation_id:
        return correlation_id
    trace_id = get_trace_id()
    correlation_id_ctx.set(trace_id)
    return trace_id


def get_causation_id() -> str | None:
    causation_id = causation_id_ctx.get()
    if causation_id:
        return causation_id
    return None


def correlation_id_for(trace_id: str) -> str:
    correlation_id = correlation_id_ctx.get()
    if correlation_id:
        return correlation_id
    return trace_id


@contextmanager
def observability_context(
    *,
    trace_id: str,
    correlation_id: str | None = None,
    causation_id: str | None = None,
) -> Iterator[None]:
    trace_token = trace_id_ctx.set(trace_id)
    correlation_token = correlation_id_ctx.set(correlation_id or trace_id)
    causation_token = causation_id_ctx.set(causation_id or "")
    try:
        yield
    finally:
        causation_id_ctx.reset(causation_token)
        correlation_id_ctx.reset(correlation_token)
        trace_id_ctx.reset(trace_token)


@contextmanager
def event_observability_context(event: EventEnvelope) -> Iterator[None]:
    with observability_context(
        trace_id=event.trace_id,
        correlation_id=event.correlation_id,
        causation_id=event.event_id,
    ):
        yield


async def tracing_middleware(
    request: Request,
    call_next: RequestResponseEndpoint,
) -> Response:
    incoming = request.headers.get(TRACE_ID_HEADER, str(uuid4()))
    correlation_id = request.headers.get(CORRELATION_ID_HEADER, incoming)
    causation_id = request.headers.get(CAUSATION_ID_HEADER)
    tracer = get_tracer("shared_kernel.http")
    route = request.scope.get("route")
    route_template = getattr(route, "path", request.url.path)
    context = extract_trace_context(request.headers)

    with tracer.start_as_current_span(
        f"{request.method} {route_template}",
        context=context,
        kind=SpanKind.SERVER,
    ) as span:
        span.set_attribute("http.method", request.method)
        span.set_attribute("http.route", route_template)
        span.set_attribute("http.target", request.url.path)
        span.set_attribute("credit.trace_id", incoming)
        span.set_attribute("credit.correlation_id", correlation_id)
        if causation_id is not None:
            span.set_attribute("credit.causation_id", causation_id)

        with observability_context(
            trace_id=incoming,
            correlation_id=correlation_id,
            causation_id=causation_id,
        ):
            try:
                response: Response = await call_next(request)
            except Exception as exc:
                span.record_exception(exc)
                span.set_status(Status(StatusCode.ERROR))
                raise

            span.set_attribute("http.status_code", response.status_code)
            if response.status_code >= 500:
                span.set_status(Status(StatusCode.ERROR))
            response.headers[TRACE_ID_HEADER] = incoming
            response.headers[CORRELATION_ID_HEADER] = correlation_id
            if causation_id is not None:
                response.headers[CAUSATION_ID_HEADER] = causation_id
            trace_headers: dict[str, str] = {}
            inject_trace_context(trace_headers)
            traceparent = trace_headers.get(TRACEPARENT_HEADER)
            if traceparent is not None:
                response.headers[TRACEPARENT_HEADER] = traceparent
            return response
