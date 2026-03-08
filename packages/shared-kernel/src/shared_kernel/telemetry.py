"""Shared OpenTelemetry provider and trace-context helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass
from urllib.parse import urlparse

from opentelemetry import propagate, trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
    OTLPSpanExporter as OTLPGrpcSpanExporter,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter as OTLPHttpSpanExporter,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import Tracer

from .config import ServiceSettings

SpanExporterFactory = Callable[[], SpanExporter]


@dataclass(frozen=True)
class _TelemetrySignature:
    enabled: bool
    endpoint: str | None
    protocol: str
    insecure: bool | None
    headers: str | None
    timeout_seconds: float
    service_name: str
    service_namespace: str
    environment: str
    sampler_ratio: float


@dataclass
class _TelemetryState:
    signature: _TelemetrySignature
    provider: TracerProvider


_telemetry_state: _TelemetryState | None = None


def configure_telemetry(
    settings: ServiceSettings,
    *,
    span_exporter_factory: SpanExporterFactory | None = None,
) -> None:
    """Configure process-local tracing for the current service runtime."""
    global _telemetry_state

    signature = _build_signature(settings)
    if not signature.enabled:
        shutdown_telemetry()
        return

    if _telemetry_state is not None and _telemetry_state.signature == signature:
        return

    shutdown_telemetry()
    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": settings.service_name,
                "service.namespace": settings.otel_service_namespace,
                "deployment.environment": settings.environment,
            }
        ),
        sampler=ParentBased(TraceIdRatioBased(settings.otel_sampler_ratio)),
    )

    exporter = (
        span_exporter_factory()
        if span_exporter_factory is not None
        else _build_span_exporter(settings)
    )
    processor = (
        SimpleSpanProcessor(exporter)
        if span_exporter_factory is not None
        else BatchSpanProcessor(exporter)
    )
    provider.add_span_processor(processor)
    _telemetry_state = _TelemetryState(signature=signature, provider=provider)


def shutdown_telemetry() -> None:
    """Tear down any configured process-local tracer provider."""
    global _telemetry_state

    state = _telemetry_state
    _telemetry_state = None
    if state is not None:
        state.provider.shutdown()


def force_flush_telemetry() -> None:
    """Flush spans synchronously when telemetry is configured."""
    state = _telemetry_state
    if state is not None:
        state.provider.force_flush()


def get_tracer(name: str) -> Tracer:
    """Return a tracer from the configured provider or a no-op tracer."""
    state = _telemetry_state
    if state is not None:
        return state.provider.get_tracer(name)
    return trace.get_tracer(name)


def extract_trace_context(headers: Mapping[str, object]) -> Context:
    """Extract W3C trace context from HTTP or broker headers."""
    normalized_headers = {
        str(key): _stringify_header_value(value)
        for key, value in headers.items()
        if _stringify_header_value(value) is not None
    }
    return propagate.extract(normalized_headers)


def inject_trace_context(headers: MutableMapping[str, str]) -> None:
    """Inject W3C trace context into outbound HTTP or broker headers."""
    propagate.inject(headers)


def current_span_identifiers() -> tuple[str | None, str | None]:
    """Return current trace/span IDs formatted for logs, if present."""
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return None, None
    return format(span_context.trace_id, "032x"), format(span_context.span_id, "016x")


def _build_signature(settings: ServiceSettings) -> _TelemetrySignature:
    return _TelemetrySignature(
        enabled=settings.otel_enabled,
        endpoint=settings.otel_exporter_otlp_endpoint,
        protocol=settings.otel_exporter_otlp_protocol,
        insecure=settings.otel_exporter_otlp_insecure,
        headers=settings.otel_exporter_otlp_headers,
        timeout_seconds=settings.otel_exporter_otlp_timeout_seconds,
        service_name=settings.service_name,
        service_namespace=settings.otel_service_namespace,
        environment=settings.environment,
        sampler_ratio=settings.otel_sampler_ratio,
    )


def _build_span_exporter(settings: ServiceSettings) -> SpanExporter:
    endpoint = _required_endpoint(settings)
    headers = _parse_otlp_headers(settings.otel_exporter_otlp_headers)
    timeout = settings.otel_exporter_otlp_timeout_seconds
    if settings.otel_exporter_otlp_protocol == "grpc":
        return OTLPGrpcSpanExporter(
            endpoint=endpoint,
            headers=headers,
            timeout=timeout,
            insecure=_grpc_export_insecure(settings, endpoint),
        )
    return OTLPHttpSpanExporter(
        endpoint=endpoint,
        headers=headers,
        timeout=timeout,
    )


def _required_endpoint(settings: ServiceSettings) -> str:
    endpoint = settings.otel_exporter_otlp_endpoint
    if endpoint is None:
        raise ValueError("OTEL exporter endpoint must be configured when telemetry is enabled")
    return endpoint


def _parse_otlp_headers(raw_headers: str | None) -> dict[str, str] | None:
    if raw_headers is None:
        return None
    parsed: dict[str, str] = {}
    for part in raw_headers.split(","):
        item = part.strip()
        if item == "":
            continue
        key, separator, value = item.partition("=")
        if separator == "" or key.strip() == "" or value.strip() == "":
            raise ValueError(
                "OTEL exporter headers must use comma-separated key=value pairs"
            )
        parsed[key.strip()] = value.strip()
    return parsed or None


def _grpc_export_insecure(settings: ServiceSettings, endpoint: str) -> bool:
    if settings.otel_exporter_otlp_insecure is not None:
        return settings.otel_exporter_otlp_insecure
    parsed = urlparse(endpoint)
    return parsed.scheme != "https"


def _stringify_header_value(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return str(value)
