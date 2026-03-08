"""api-gateway service entrypoint."""

from __future__ import annotations

from fastapi import FastAPI

from shared_kernel import create_service_app, load_settings
from shared_kernel.dependencies import build_default_startup_checks

from .auth import build_gateway_auth_startup_checks
from .routes import router


def build_router(app: FastAPI) -> None:
    app.include_router(router)


settings = load_settings("api-gateway")
app = create_service_app(
    settings=settings,
    startup_checks=build_default_startup_checks(settings)
    + build_gateway_auth_startup_checks(settings),
    router_builder=build_router,
)
