from __future__ import annotations

import pytest
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from shared_kernel import (
    ServiceSettings,
    configure_telemetry,
    force_flush_telemetry,
    get_tracer,
    shutdown_telemetry,
)
from tests.defaults import DEFAULT_POSTGRES_DSN, DEFAULT_RABBITMQ_URL


def _settings() -> ServiceSettings:
    return ServiceSettings(
        service_name="telemetry-test",
        postgres_dsn=DEFAULT_POSTGRES_DSN,
        rabbitmq_url=DEFAULT_RABBITMQ_URL,
        otel_enabled=True,
        otel_exporter_otlp_endpoint="http://collector.internal:4318/v1/traces",
    )


@pytest.mark.unit
def test_configure_telemetry_exports_spans_with_service_resource_attributes() -> None:
    exporter = InMemorySpanExporter()
    configure_telemetry(_settings(), span_exporter_factory=lambda: exporter)

    try:
        with get_tracer("tests.telemetry").start_as_current_span("root-span") as span:
            span.set_attribute("test.attribute", "value")

        force_flush_telemetry()
        spans = exporter.get_finished_spans()
        assert len(spans) == 1
        finished_span = spans[0]
        assert finished_span.name == "root-span"
        assert finished_span.attributes is not None
        assert finished_span.attributes["test.attribute"] == "value"
        assert finished_span.resource.attributes["service.name"] == "telemetry-test"
        assert finished_span.resource.attributes["service.namespace"] == "credit-ai-ops"
        assert finished_span.resource.attributes["deployment.environment"] == "local"
    finally:
        shutdown_telemetry()


@pytest.mark.unit
def test_build_span_exporter_uses_grpc_protocol_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class _FakeGrpcExporter(SpanExporter):
        def __init__(
            self,
            *,
            endpoint: str,
            headers: dict[str, str] | None,
            timeout: float,
            insecure: bool,
        ) -> None:
            captured.update(
                {
                    "endpoint": endpoint,
                    "headers": headers,
                    "timeout": timeout,
                    "insecure": insecure,
                }
            )

        def export(self, spans: object) -> SpanExportResult:
            _ = spans
            return SpanExportResult.SUCCESS

        def shutdown(self) -> None:
            return None

    monkeypatch.setattr("shared_kernel.telemetry.OTLPGrpcSpanExporter", _FakeGrpcExporter)
    configure_telemetry(
        _settings().model_copy(
            update={
                "otel_exporter_otlp_protocol": "grpc",
                "otel_exporter_otlp_endpoint": "http://collector.internal:4317",
                "otel_exporter_otlp_headers": "authorization=Bearer token",
            }
        )
    )

    try:
        assert captured == {
            "endpoint": "http://collector.internal:4317",
            "headers": {"authorization": "Bearer token"},
            "timeout": 5.0,
            "insecure": True,
        }
    finally:
        shutdown_telemetry()
