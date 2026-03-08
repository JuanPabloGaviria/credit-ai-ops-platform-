import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from shared_kernel import ServiceError, ServiceSettings, create_service_app
from shared_kernel.telemetry import configure_telemetry as configure_runtime_telemetry


async def _boom_service() -> dict[str, str]:
    raise ServiceError(
        error_code="KNOWN_FAILURE",
        message="known failure",
        operation="boom_service",
        status_code=418,
    )


async def _boom_unexpected() -> dict[str, str]:
    raise RuntimeError("unexpected crash")


def _settings(
    *,
    service_name: str = "test-service",
    postgres_dsn: str = "postgresql://db.example:5432/credit_ai_ops",
    rabbitmq_url: str = "amqp://mq.example:5672/",
    skip_startup_dependency_checks: bool = True,
) -> ServiceSettings:
    return ServiceSettings(
        service_name=service_name,
        postgres_dsn=postgres_dsn,
        rabbitmq_url=rabbitmq_url,
        skip_startup_dependency_checks=skip_startup_dependency_checks,
    )


@pytest.mark.unit
def test_app_factory_skips_startup_checks_when_flag_enabled() -> None:
    check_invocations = 0

    async def failing_check() -> None:
        nonlocal check_invocations
        check_invocations += 1
        raise RuntimeError("startup failure")

    app = create_service_app(_settings(), startup_checks=[failing_check])

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert check_invocations == 0


@pytest.mark.unit
def test_app_factory_returns_typed_service_error_envelope() -> None:
    def router_builder(app: FastAPI) -> None:
        app.add_api_route("/boom-service", _boom_service, methods=["GET"])

    app = create_service_app(
        _settings(
            service_name="test-service-service-error",
            skip_startup_dependency_checks=True,
        ),
        startup_checks=[],
        router_builder=router_builder,
    )

    with TestClient(app) as client:
        response = client.get("/boom-service")

    payload = response.json()
    assert response.status_code == 418
    assert payload["error_code"] == "KNOWN_FAILURE"
    assert payload["service"] == "test-service-service-error"
    assert response.headers["x-correlation-id"] == response.headers["x-trace-id"]


@pytest.mark.unit
def test_app_factory_returns_unexpected_error_envelope() -> None:
    def router_builder(app: FastAPI) -> None:
        app.add_api_route("/boom-unexpected", _boom_unexpected, methods=["GET"])

    app = create_service_app(
        _settings(
            service_name="test-service-unexpected-error",
            skip_startup_dependency_checks=True,
        ),
        startup_checks=[],
        router_builder=router_builder,
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom-unexpected")

    payload = response.json()
    assert response.status_code == 500
    assert payload["error_code"] == "UNEXPECTED_ERROR"
    assert payload["service"] == "test-service-unexpected-error"


@pytest.mark.unit
def test_app_factory_records_metrics_for_error_responses() -> None:
    def router_builder(app: FastAPI) -> None:
        app.add_api_route("/boom-service", _boom_service, methods=["GET"])

    app = create_service_app(
        _settings(
            service_name="test-service-metrics-error",
            skip_startup_dependency_checks=True,
        ),
        startup_checks=[],
        router_builder=router_builder,
    )

    with TestClient(app) as client:
        failure_response = client.get("/boom-service")
        metrics_response = client.get("/metrics")

    assert failure_response.status_code == 418
    assert metrics_response.status_code == 200
    assert 'service="test_service_metrics_error"' in metrics_response.text
    assert 'route="boom_service"' in metrics_response.text
    assert 'status_code="418"' in metrics_response.text


@pytest.mark.unit
def test_app_factory_emits_traceparent_when_telemetry_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _configure_with_memory_exporter(settings: ServiceSettings) -> None:
        configure_runtime_telemetry(settings, span_exporter_factory=InMemorySpanExporter)

    monkeypatch.setattr(
        "shared_kernel.app_factory.configure_telemetry",
        _configure_with_memory_exporter,
    )

    app = create_service_app(
        _settings(
            service_name="test-service-telemetry",
            skip_startup_dependency_checks=True,
        ).model_copy(
            update={
                "otel_enabled": True,
                "otel_exporter_otlp_endpoint": "http://collector.internal:4318/v1/traces",
            }
        ),
        startup_checks=[],
    )

    with TestClient(app) as client:
        response = client.get("/ready")

    assert response.status_code == 200
    assert response.headers["traceparent"].startswith("00-")
