"""FastAPI service factory enforcing shared reliability and observability contracts."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import cast

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from prometheus_client import generate_latest
from starlette.middleware.base import RequestResponseEndpoint

from contracts import ErrorEnvelope
from observability import MetricsRegistry

from .config import ServiceSettings
from .dependencies import (
    ProbeCallable,
    build_default_startup_checks,
    run_startup_checks,
)
from .errors import ServiceError
from .logging import configure_logging, get_logger
from .telemetry import configure_telemetry, shutdown_telemetry
from .tracing import get_trace_id, tracing_middleware


def create_service_app(
    settings: ServiceSettings,
    startup_checks: list[ProbeCallable] | None = None,
    router_builder: Callable[[FastAPI], None] | None = None,
) -> FastAPI:
    """Create a service app with standardized middleware and error handling."""
    configure_logging(settings.log_level)
    logger = get_logger(settings.service_name)
    metrics = MetricsRegistry(settings.service_name)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        configure_telemetry(settings)
        checks = (
            startup_checks if startup_checks is not None else build_default_startup_checks(settings)
        )
        if settings.skip_startup_dependency_checks:
            checks = []
        await run_startup_checks(checks, settings.service_name)
        try:
            yield
        finally:
            shutdown_telemetry()

    app = FastAPI(title=settings.service_name, version=settings.app_version, lifespan=lifespan)

    async def trace_middleware(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        return await tracing_middleware(request, call_next)

    app.middleware("http")(trace_middleware)

    async def metrics_middleware(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        started = time.perf_counter()
        status_code = 500
        try:
            response: Response = await call_next(request)
            status_code = response.status_code
            return response
        except ServiceError as exc:
            status_code = exc.status_code
            raise
        finally:
            duration = time.perf_counter() - started
            route = request.scope.get("route")
            route_template = getattr(route, "path", request.url.path)
            metrics.observe_request(
                route=route_template,
                method=request.method,
                status_code=status_code,
                latency_seconds=duration,
            )

    app.middleware("http")(metrics_middleware)

    async def handle_service_error(_: Request, exc: Exception) -> JSONResponse:
        typed_exc = cast(ServiceError, exc)
        envelope = typed_exc.to_envelope(service=settings.service_name, trace_id=get_trace_id())
        logger.error("service_error", **envelope.model_dump(mode="json"))
        return JSONResponse(
            status_code=typed_exc.status_code,
            content=envelope.model_dump(mode="json"),
        )

    app.add_exception_handler(ServiceError, handle_service_error)

    async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        envelope = ErrorEnvelope(
            error_code="UNEXPECTED_ERROR",
            message="Unexpected runtime failure",
            service=settings.service_name,
            operation="unhandled_exception",
            trace_id=get_trace_id(),
            cause=str(exc),
            hint=(
                "Inspect structured logs with trace ID to identify failing dependency or code path"
            ),
        )
        logger.error("unexpected_error", **envelope.model_dump(mode="json"))
        return JSONResponse(status_code=500, content=envelope.model_dump(mode="json"))

    app.add_exception_handler(Exception, handle_unexpected)

    async def health() -> dict[str, str]:
        return {"status": "ok", "service": settings.service_name}

    app.add_api_route("/health", health, methods=["GET"], tags=["system"])

    async def ready() -> dict[str, str]:
        return {"status": "ready", "service": settings.service_name}

    app.add_api_route("/ready", ready, methods=["GET"], tags=["system"])

    async def metrics_endpoint() -> PlainTextResponse:
        return PlainTextResponse(generate_latest().decode("utf-8"), media_type="text/plain")

    app.add_api_route("/metrics", metrics_endpoint, methods=["GET"], tags=["system"])

    if router_builder is not None:
        router_builder(app)

    return app
