"""Messaging constants to avoid string drift across services."""

EXCHANGE_CREDIT_EVENTS = "credit.events"
EXCHANGE_CREDIT_EVENTS_DLX = "credit.events.dlx"

EVENT_CREDIT_APPLICATION_SUBMITTED = "credit.application.submitted.v1"
EVENT_CREDIT_FEATURE_MATERIALIZED = "credit.feature.materialized.v1"
EVENT_CREDIT_SCORING_GENERATED = "credit.scoring.generated.v1"
EVENT_CREDIT_DECISION_MADE = "credit.decision.made.v1"
EVENT_CREDIT_ASSISTANT_SUMMARIZED = "credit.assistant.summarized.v1"
EVENT_CREDIT_DECISION_OVERRIDDEN = "credit.decision.overridden.v1"
EVENT_CREDIT_MODEL_PROMOTED = "credit.model.promoted.v1"
EVENT_CREDIT_DRIFT_DETECTED = "credit.drift.detected.v1"

ROUTING_CREDIT_ALL = "credit.#"

QUEUE_FEATURE_APPLICATION_SUBMITTED = "feature.application_submitted"
QUEUE_SCORING_FEATURE_MATERIALIZED = "scoring.feature_materialized"
QUEUE_DECISION_SCORING_GENERATED = "decision.scoring_generated"
QUEUE_ASSISTANT_DECISION_MADE = "assistant.decision_made"
QUEUE_AUDIT_CREDIT_EVENTS = "audit.credit_events"
