"""Shared kernel for all services."""

from .app_factory import create_service_app
from .artifacts import (
    ArtifactStore,
    ArtifactStoreError,
    build_artifact_store,
    build_filesystem_artifact_store,
)
from .assistant import summarize_case
from .auth import (
    AuthenticatedPrincipal,
    authorize_request,
    build_auth_startup_checks,
    build_service_authorization,
)
from .config import ServiceSettings, load_settings
from .credit_policy import decide_credit, materialize_features, score_application
from .database import DatabaseClient, DatabaseExecutor
from .errors import ServiceError
from .idempotency import normalize_optional_idempotency_key, require_idempotency_key
from .messaging import RabbitMQClient, build_rabbitmq_client
from .ml_reproducibility import build_model_metadata
from .outbox import (
    ClaimedOutboxEvent,
    enqueue_outbox_event,
    fetch_pending_outbox_events,
    mark_outbox_event_failed,
    mark_outbox_event_published,
    record_inbox_event,
)
from .outbox_relay import OutboxRelayConfig, OutboxRelayWorker
from .telemetry import (
    configure_telemetry,
    force_flush_telemetry,
    get_tracer,
    shutdown_telemetry,
)
from .tracing import (
    correlation_id_for,
    event_observability_context,
    get_causation_id,
    get_correlation_id,
    get_trace_id,
    observability_context,
)

__all__ = [
    "ArtifactStore",
    "ArtifactStoreError",
    "AuthenticatedPrincipal",
    "ClaimedOutboxEvent",
    "DatabaseClient",
    "DatabaseExecutor",
    "OutboxRelayConfig",
    "OutboxRelayWorker",
    "RabbitMQClient",
    "ServiceError",
    "ServiceSettings",
    "authorize_request",
    "build_artifact_store",
    "build_auth_startup_checks",
    "build_filesystem_artifact_store",
    "build_model_metadata",
    "build_rabbitmq_client",
    "build_service_authorization",
    "configure_telemetry",
    "correlation_id_for",
    "create_service_app",
    "decide_credit",
    "enqueue_outbox_event",
    "event_observability_context",
    "fetch_pending_outbox_events",
    "force_flush_telemetry",
    "get_causation_id",
    "get_correlation_id",
    "get_trace_id",
    "get_tracer",
    "load_settings",
    "mark_outbox_event_failed",
    "mark_outbox_event_published",
    "materialize_features",
    "normalize_optional_idempotency_key",
    "observability_context",
    "record_inbox_event",
    "require_idempotency_key",
    "score_application",
    "shutdown_telemetry",
    "summarize_case",
]
