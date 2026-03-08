"""Persistence and outbox adapter for feature service."""

from __future__ import annotations

from uuid import uuid4

from contracts import (
    EVENT_CREDIT_FEATURE_MATERIALIZED,
    ApplicationInput,
    EventEnvelope,
    FeatureVector,
)
from shared_kernel import (
    DatabaseClient,
    DatabaseExecutor,
    RabbitMQClient,
    ServiceSettings,
    build_rabbitmq_client,
    correlation_id_for,
    enqueue_outbox_event,
    fetch_pending_outbox_events,
    get_causation_id,
    mark_outbox_event_published,
    materialize_features,
    record_inbox_event,
)


class FeatureRepository:
    """Feature materialization repository with inbox/outbox semantics."""

    def __init__(self, settings: ServiceSettings) -> None:
        self._settings = settings
        self._db = DatabaseClient(settings.postgres_dsn)
        self._broker: RabbitMQClient = build_rabbitmq_client(settings)

    async def connect(self) -> None:
        await self._db.connect()
        await self._broker.connect()

    async def close(self) -> None:
        await self._broker.close()
        await self._db.close()

    async def materialize_from_application(
        self,
        application: ApplicationInput,
        trace_id: str,
        db: DatabaseExecutor | None = None,
        *,
        source_event_id: str | None = None,
    ) -> FeatureVector:
        if db is None:
            async with self._db.transaction() as tx:
                return await self.materialize_from_application(
                    application=application,
                    trace_id=trace_id,
                    db=tx,
                    source_event_id=source_event_id,
                )

        features = materialize_features(application)
        feature_event_id = str(uuid4())
        materialization_source = (
            "application_event" if source_event_id is not None else "manual_request"
        )
        await db.execute(
            """
            INSERT INTO feature_vector_history (
                materialization_id,
                application_id,
                requested_amount,
                debt_to_income,
                amount_to_income,
                credit_history_months,
                existing_defaults,
                trace_id,
                feature_event_id,
                source_event_id,
                materialization_source,
                materialized_at,
                created_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, NOW(), NOW())
            """,
            str(uuid4()),
            features.application_id,
            features.requested_amount,
            features.debt_to_income,
            features.amount_to_income,
            features.credit_history_months,
            features.existing_defaults,
            trace_id,
            feature_event_id,
            source_event_id,
            materialization_source,
        )

        event = EventEnvelope(
            event_name=EVENT_CREDIT_FEATURE_MATERIALIZED,
            event_id=feature_event_id,
            trace_id=trace_id,
            correlation_id=correlation_id_for(trace_id),
            causation_id=get_causation_id() if source_event_id is None else source_event_id,
            producer="feature-service",
            payload=features.model_dump(mode="json"),
        )
        await enqueue_outbox_event(db, "feature_outbox", event)
        return features

    async def handle_submitted_event(self, event: EventEnvelope) -> bool:
        first_seen = False
        async with self._db.transaction() as tx:
            first_seen = await record_inbox_event(tx, "feature_inbox", event)
            if first_seen:
                application = ApplicationInput.model_validate(event.payload)
                await self.materialize_from_application(
                    application=application,
                    trace_id=event.trace_id,
                    db=tx,
                    source_event_id=event.event_id,
                )

        return first_seen

    async def flush_outbox(self) -> int:
        pending_events = await fetch_pending_outbox_events(
            self._db,
            "feature_outbox",
            lease_seconds=self._settings.outbox_relay_claim_lease_seconds,
        )
        published = 0
        for claimed_event in pending_events:
            await self._broker.publish_event(claimed_event.event)
            await mark_outbox_event_published(
                self._db,
                "feature_outbox",
                claimed_event.event.event_id,
                claim_token=claimed_event.claim_token,
            )
            published += 1
        return published
