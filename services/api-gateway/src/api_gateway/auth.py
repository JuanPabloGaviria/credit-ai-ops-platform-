"""Gateway auth wrappers over shared JWT authorization helpers."""

from __future__ import annotations

from shared_kernel import (
    AuthenticatedPrincipal,
    ServiceError,
    ServiceSettings,
    authorize_request,
    build_auth_startup_checks,
)
from shared_kernel.dependencies import ProbeCallable


async def authorize_gateway_request(
    *,
    settings: ServiceSettings,
    authorization: str | None,
    operation: str,
) -> AuthenticatedPrincipal:
    """Validate inbound bearer token when gateway auth is enabled."""
    return await authorize_request(
        settings=settings,
        authorization=authorization,
        operation=operation,
    )


def build_gateway_auth_startup_checks(settings: ServiceSettings) -> list[ProbeCallable]:
    """Build gateway auth startup checks using shared JWT validation config."""
    checks = build_auth_startup_checks(settings)
    if settings.auth_mode == "disabled":
        return checks

    async def service_token_config_probe() -> None:
        if settings.auth_shared_secret:
            return
        if not settings.auth_service_token_url:
            raise ServiceError(
                error_code="AUTH_CONFIG_INVALID",
                message="Gateway requires AUTH_SERVICE_TOKEN_URL when auth is enabled",
                operation="gateway_service_auth_config",
                status_code=500,
            )
        if not settings.auth_service_client_id:
            raise ServiceError(
                error_code="AUTH_CONFIG_INVALID",
                message="Gateway requires AUTH_SERVICE_CLIENT_ID when auth is enabled",
                operation="gateway_service_auth_config",
                status_code=500,
            )
        if not settings.auth_service_client_secret:
            raise ServiceError(
                error_code="AUTH_CONFIG_INVALID",
                message="Gateway requires AUTH_SERVICE_CLIENT_SECRET when auth is enabled",
                operation="gateway_service_auth_config",
                status_code=500,
            )

    return [*checks, service_token_config_probe]
