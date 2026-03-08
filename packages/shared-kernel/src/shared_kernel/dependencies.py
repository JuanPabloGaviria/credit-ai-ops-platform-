"""Startup dependency probes for fail-fast boot semantics."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from urllib.parse import urlparse

from .config import ServiceSettings
from .errors import ServiceError

ProbeCallable = Callable[[], Awaitable[None]]


async def run_startup_checks(checks: list[ProbeCallable], service_name: str) -> None:
    """Run all dependency checks and fail startup with explicit context on first failure."""
    for check in checks:
        try:
            await check()
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError(
                error_code="STARTUP_DEPENDENCY_FAILURE",
                message="Startup dependency check failed",
                operation="startup_checks",
                status_code=503,
                cause=f"{service_name}: {exc}",
                hint="Inspect dependency endpoints, credentials, or network connectivity",
            ) from exc


def build_default_startup_checks(settings: ServiceSettings) -> list[ProbeCallable]:
    """Build default reachability probes for mandatory dependencies."""

    async def postgres_probe() -> None:
        await _probe_tcp_endpoint(
            settings.postgres_dsn,
            operation="postgres_probe",
            timeout_seconds=settings.startup_probe_timeout_seconds,
        )

    async def rabbitmq_probe() -> None:
        await _probe_tcp_endpoint(
            settings.rabbitmq_url,
            operation="rabbitmq_probe",
            timeout_seconds=settings.startup_probe_timeout_seconds,
        )

    return [postgres_probe, rabbitmq_probe]


async def _probe_tcp_endpoint(url: str, operation: str, timeout_seconds: float) -> None:
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port or _default_port(parsed.scheme)
    if host is None or port is None:
        raise ServiceError(
            error_code="INVALID_DEPENDENCY_DSN",
            message="Dependency connection URL is missing host or port",
            operation=operation,
            status_code=500,
            cause=url,
            hint="Verify DSN format includes protocol://host:port",
        )
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host=host, port=port),
            timeout=timeout_seconds,
        )
        writer.close()
        await writer.wait_closed()
        _ = reader
    except Exception as exc:
        raise ServiceError(
            error_code="DEPENDENCY_UNREACHABLE",
            message="Dependency is not reachable during startup checks",
            operation=operation,
            status_code=503,
            cause=f"{host}:{port} -> {exc}",
            hint=(
                "Bring dependency online or set SKIP_STARTUP_DEPENDENCY_CHECKS=true for tests only"
            ),
        ) from exc


def _default_port(scheme: str) -> int | None:
    return {
        "postgresql": 5432,
        "postgres": 5432,
        "amqp": 5672,
        "amqps": 5671,
        "http": 80,
        "https": 443,
    }.get(scheme.lower())
