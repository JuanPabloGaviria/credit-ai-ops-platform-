"""Typed service configuration with fail-fast validation."""

from __future__ import annotations

import os
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServiceSettings(BaseSettings):
    """Base service settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
    )

    service_name: str = Field(min_length=3)
    app_version: str = Field(default="0.1.0", min_length=1)
    environment: str = Field(default="local", pattern=r"^(local|dev|staging|prod)$")
    log_level: str = Field(default="INFO")

    postgres_dsn: str = Field(min_length=8)
    rabbitmq_url: str = Field(min_length=8)

    request_timeout_seconds: float = Field(default=3.0, ge=0.1, le=60.0)
    startup_probe_timeout_seconds: float = Field(default=2.0, ge=0.1, le=30.0)
    retry_max_attempts: int = Field(default=3, ge=1, le=10)
    retry_base_delay_seconds: float = Field(default=0.1, ge=0.01, le=10.0)
    retry_max_delay_seconds: float = Field(default=5.0, ge=0.1, le=120.0)
    retry_jitter_seconds: float = Field(default=0.2, ge=0.0, le=10.0)
    broker_circuit_failure_threshold: int = Field(default=5, ge=1, le=100)
    broker_circuit_success_threshold: int = Field(default=2, ge=1, le=100)
    broker_circuit_recovery_timeout_seconds: float = Field(default=15.0, ge=0.5, le=300.0)
    broker_bulkhead_max_concurrency: int = Field(default=10, ge=1, le=1000)
    broker_prefetch_count: int = Field(default=10, ge=1, le=1000)
    outbox_relay_batch_size: int = Field(default=100, ge=1, le=1000)
    outbox_relay_poll_interval_seconds: float = Field(default=0.5, ge=0.1, le=30.0)
    outbox_relay_claim_lease_seconds: int = Field(default=30, ge=5, le=3600)
    outbox_relay_max_publish_attempts: int = Field(default=5, ge=1, le=100)
    idempotency_ttl_seconds: int = Field(default=300, ge=30, le=86_400)
    idempotency_stale_after_seconds: int = Field(default=120, ge=5, le=3_600)
    auth_mode: str = Field(default="disabled", pattern=r"^(disabled|required)$")
    auth_issuer: str | None = None
    auth_jwks_url: str | None = None
    auth_shared_secret: str | None = None
    auth_required_scope: str | None = None
    auth_required_audience: str | None = None
    auth_clock_skew_seconds: int = Field(default=60, ge=0, le=600)
    auth_service_token_url: str | None = None
    auth_service_client_id: str | None = None
    auth_service_client_secret: str | None = None
    auth_service_scope: str | None = None
    auth_service_audience: str | None = None
    keycloak_url: str | None = None
    keycloak_realm: str | None = None
    keycloak_client_id: str | None = None
    keycloak_client_secret: str | None = None
    keycloak_introspection_url: str | None = None
    keycloak_required_scope: str | None = None
    keycloak_required_audience: str | None = None
    keycloak_request_timeout_seconds: float = Field(default=2.0, ge=0.1, le=30.0)
    feature_service_url: str = Field(default="http://localhost:8002", min_length=8)
    scoring_service_url: str = Field(default="http://localhost:8003", min_length=8)
    decision_service_url: str = Field(default="http://localhost:8004", min_length=8)
    scoring_model_name: str = Field(default="credit-risk", min_length=3)
    scoring_model_stage: str = Field(default="production", pattern=r"^(staging|production)$")
    artifact_storage_backend: str = Field(
        default="filesystem",
        pattern=r"^(filesystem|azure_blob)$",
    )
    artifact_root_dir: str = Field(default="build/mlops", min_length=3)
    artifact_blob_account_url: str | None = None
    artifact_blob_container_name: str | None = None
    artifact_blob_managed_identity_client_id: str | None = None
    mlops_min_auc: float = Field(default=0.78, ge=0, le=1)
    mlops_max_calibration_error: float = Field(default=0.06, ge=0, le=1)
    model_signing_private_key_pem: str | None = None
    model_signing_public_key_pem: str | None = None
    model_signing_key_id: str = Field(default="credit-models-ed25519", min_length=3, max_length=128)
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str | None = None
    otel_exporter_otlp_protocol: str = Field(
        default="http/protobuf",
        pattern=r"^(http/protobuf|grpc)$",
    )
    otel_exporter_otlp_insecure: bool | None = None
    otel_exporter_otlp_headers: str | None = None
    otel_exporter_otlp_timeout_seconds: float = Field(default=5.0, ge=0.1, le=60.0)
    otel_service_namespace: str = Field(default="credit-ai-ops", min_length=3)
    otel_sampler_ratio: float = Field(default=1.0, ge=0.0, le=1.0)

    enable_llm: bool = False
    enable_pii_logging: bool = False
    skip_startup_dependency_checks: bool = False

    @model_validator(mode="after")
    def apply_auth_compatibility_defaults(self) -> ServiceSettings:
        issuer = _normalized(self.auth_issuer)
        keycloak_issuer = _keycloak_issuer(self.keycloak_url, self.keycloak_realm)
        if issuer is None and keycloak_issuer is not None:
            issuer = keycloak_issuer
            self.auth_issuer = issuer

        if _normalized(self.auth_jwks_url) is None and issuer is not None:
            self.auth_jwks_url = (
                f"{issuer.rstrip('/')}/protocol/openid-connect/certs"
                if _is_keycloak_issuer(issuer)
                else None
            )

        if _normalized(self.auth_required_scope) is None:
            self.auth_required_scope = _normalized(self.keycloak_required_scope)
        if _normalized(self.auth_required_audience) is None:
            self.auth_required_audience = _normalized(self.keycloak_required_audience)
        if _normalized(self.auth_service_client_id) is None:
            self.auth_service_client_id = _normalized(self.keycloak_client_id)
        if _normalized(self.auth_service_client_secret) is None:
            self.auth_service_client_secret = _normalized(self.keycloak_client_secret)
        if _normalized(self.auth_service_token_url) is None and issuer is not None:
            self.auth_service_token_url = (
                f"{issuer.rstrip('/')}/protocol/openid-connect/token"
                if _is_keycloak_issuer(issuer)
                else None
            )
        self.artifact_blob_account_url = _normalized(self.artifact_blob_account_url)
        self.artifact_blob_container_name = _normalized(self.artifact_blob_container_name)
        self.artifact_blob_managed_identity_client_id = _normalized(
            self.artifact_blob_managed_identity_client_id
        )
        self.otel_exporter_otlp_endpoint = _normalized(self.otel_exporter_otlp_endpoint)
        self.otel_exporter_otlp_headers = _normalized(self.otel_exporter_otlp_headers)
        self.model_signing_private_key_pem = _normalized(self.model_signing_private_key_pem)
        self.model_signing_public_key_pem = _normalized(self.model_signing_public_key_pem)
        if self.artifact_storage_backend == "azure_blob":
            if self.artifact_blob_account_url is None:
                raise ValueError(
                    "artifact_blob_account_url is required when artifact_storage_backend=azure_blob"
                )
            if self.artifact_blob_container_name is None:
                raise ValueError(
                    "artifact_blob_container_name is required when "
                    "artifact_storage_backend=azure_blob"
                )
        if self.otel_enabled and self.otel_exporter_otlp_endpoint is None:
            raise ValueError("OTEL exporter endpoint is required when telemetry is enabled")
        return self


def load_settings(service_name: str) -> ServiceSettings:
    """Load settings with explicit service identity."""
    return ServiceSettings(
        service_name=service_name,
        postgres_dsn=os.getenv("POSTGRES_DSN", ""),
        rabbitmq_url=os.getenv("RABBITMQ_URL", ""),
    )


def _normalized(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if stripped == "":
        return None
    return stripped


def _keycloak_issuer(keycloak_url: str | None, keycloak_realm: str | None) -> str | None:
    normalized_url = _normalized(keycloak_url)
    normalized_realm = _normalized(keycloak_realm)
    if normalized_url is None or normalized_realm is None:
        return None
    return f"{normalized_url.rstrip('/')}/realms/{normalized_realm}"


def _is_keycloak_issuer(issuer: str) -> bool:
    parsed = urlparse(issuer)
    return parsed.scheme in {"http", "https"} and "/realms/" in parsed.path
