import asyncio
from typing import Any

import pytest

from shared_kernel.config import ServiceSettings
from shared_kernel.dependencies import (
    build_default_startup_checks,
    run_startup_checks,
)
from shared_kernel.errors import ServiceError


@pytest.mark.unit
def test_run_startup_checks_wraps_unknown_failure_with_context() -> None:
    async def failing_check() -> None:
        raise RuntimeError("socket timeout")

    with pytest.raises(ServiceError) as error:
        asyncio.run(run_startup_checks([failing_check], "application-service"))

    assert error.value.error_code == "STARTUP_DEPENDENCY_FAILURE"
    assert "application-service" in (error.value.cause or "")


@pytest.mark.unit
def test_run_startup_checks_preserves_typed_service_error() -> None:
    async def failing_check() -> None:
        raise ServiceError(
            error_code="DEPENDENCY_UNREACHABLE",
            message="dependency down",
            operation="startup_checks",
            status_code=503,
        )

    with pytest.raises(ServiceError) as error:
        asyncio.run(run_startup_checks([failing_check], "feature-service"))

    assert error.value.error_code == "DEPENDENCY_UNREACHABLE"


@pytest.mark.unit
def test_build_default_startup_checks_uses_configured_probe_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_calls: list[tuple[str, str, float]] = []

    async def fake_probe(url: str, operation: str, timeout_seconds: float) -> None:
        captured_calls.append((url, operation, timeout_seconds))

    monkeypatch.setattr("shared_kernel.dependencies._probe_tcp_endpoint", fake_probe)
    settings = ServiceSettings(
        service_name="application-service",
        postgres_dsn="postgresql://db.example:5432/credit_ai_ops",
        rabbitmq_url="amqp://mq.example:5672/",
        startup_probe_timeout_seconds=7.5,
    )

    async def scenario() -> None:
        checks = build_default_startup_checks(settings)
        await checks[0]()
        await checks[1]()

    asyncio.run(scenario())

    assert captured_calls == [
        ("postgresql://db.example:5432/credit_ai_ops", "postgres_probe", 7.5),
        ("amqp://mq.example:5672/", "rabbitmq_probe", 7.5),
    ]


@pytest.mark.unit
def test_probe_tcp_endpoint_rejects_missing_host_or_port() -> None:
    settings = ServiceSettings(
        service_name="application-service",
        postgres_dsn="postgresql:///credit_ai_ops",
        rabbitmq_url="amqp://mq.example:5672/",
        startup_probe_timeout_seconds=2.0,
    )

    async def scenario() -> None:
        checks = build_default_startup_checks(settings)
        await checks[0]()

    with pytest.raises(ServiceError) as error:
        asyncio.run(scenario())

    assert error.value.error_code == "INVALID_DEPENDENCY_DSN"


@pytest.mark.unit
def test_probe_tcp_endpoint_wraps_connectivity_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = ServiceSettings(
        service_name="application-service",
        postgres_dsn="postgresql://db.example:5432/credit_ai_ops",
        rabbitmq_url="amqp://mq.example:5672/",
        startup_probe_timeout_seconds=1.0,
    )

    async def fail_connection(**_kwargs: Any) -> tuple[object, object]:
        raise OSError("connection refused")

    monkeypatch.setattr(asyncio, "open_connection", fail_connection)

    async def scenario() -> None:
        checks = build_default_startup_checks(settings)
        await checks[1]()

    with pytest.raises(ServiceError) as error:
        asyncio.run(scenario())

    assert error.value.error_code == "DEPENDENCY_UNREACHABLE"


@pytest.mark.unit
def test_probe_tcp_endpoint_succeeds_when_connection_is_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = ServiceSettings(
        service_name="application-service",
        postgres_dsn="postgresql://db.example:5432/credit_ai_ops",
        rabbitmq_url="amqp://mq.example:5672/",
        startup_probe_timeout_seconds=1.0,
    )

    class _FakeWriter:
        def close(self) -> None:
            return None

        async def wait_closed(self) -> None:
            return None

    async def open_connection(**_kwargs: Any) -> tuple[object, _FakeWriter]:
        return object(), _FakeWriter()

    monkeypatch.setattr(asyncio, "open_connection", open_connection)

    async def scenario() -> None:
        checks = build_default_startup_checks(settings)
        await checks[0]()

    asyncio.run(scenario())
