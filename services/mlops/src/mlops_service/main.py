"""mlops service entrypoint."""

from __future__ import annotations

from fastapi import FastAPI

from shared_kernel import (
    ArtifactStoreError,
    ServiceError,
    build_artifact_store,
    build_auth_startup_checks,
    create_service_app,
    load_settings,
)
from shared_kernel.dependencies import build_default_startup_checks

from .routes import router


def build_router(app: FastAPI) -> None:
    app.include_router(router)


settings = load_settings("mlops")


async def _require_model_signing_private_key() -> None:
    if settings.model_signing_private_key_pem is None:
        raise ServiceError(
            error_code="ML_MODEL_SIGNING_PRIVATE_KEY_MISSING",
            message="mlops service requires MODEL_SIGNING_PRIVATE_KEY_PEM at startup",
            operation="mlops_startup_model_signing_check",
            status_code=500,
            hint="Configure the mlops signing private key before registering models",
        )


async def _require_artifact_store_backend() -> None:
    try:
        _ = build_artifact_store(settings)
    except (ArtifactStoreError, ValueError) as exc:
        raise ServiceError(
            error_code="ML_ARTIFACT_STORAGE_CONFIGURATION_INVALID",
            message="mlops service artifact storage configuration is invalid",
            operation="mlops_startup_artifact_store_check",
            status_code=500,
            cause=str(exc),
            hint="Configure filesystem or Azure Blob artifact storage before startup",
        ) from exc


app = create_service_app(
    settings=settings,
    startup_checks=(
        build_default_startup_checks(settings)
        + build_auth_startup_checks(settings)
        + [_require_model_signing_private_key, _require_artifact_store_backend]
    ),
    router_builder=build_router,
)
