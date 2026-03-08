"""Persistence and outbox adapter for scoring service."""

from __future__ import annotations

from uuid import uuid4

from contracts import (
    EVENT_CREDIT_SCORING_GENERATED,
    EventEnvelope,
    FeatureVector,
    ScorePrediction,
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
    record_inbox_event,
)

from .runtime import resolve_active_scoring_model


class ScoringRepository:
    """Scoring repository with inbox/outbox semantics."""

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

    async def score_features(
        self,
        features: FeatureVector,
        trace_id: str,
        db: DatabaseExecutor | None = None,
        *,
        source_event_id: str | None = None,
    ) -> ScorePrediction:
        if db is None:
            async with self._db.transaction() as tx:
                return await self.score_features(
                    features=features,
                    trace_id=trace_id,
                    db=tx,
                    source_event_id=source_event_id,
                )

        active_model = await resolve_active_scoring_model(db=db, settings=self._settings)
        prediction = active_model.score(features)
        scoring_event_id = str(uuid4())
        scoring_source = "feature_event" if source_event_id is not None else "manual_request"

        await db.execute(
            """
            INSERT INTO score_prediction_history (
                prediction_id,
                application_id,
                requested_amount,
                risk_score,
                model_version,
                reason_codes,
                trace_id,
                scoring_event_id,
                source_event_id,
                scoring_source,
                scored_at,
                created_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW(), NOW())
            """,
            str(uuid4()),
            prediction.application_id,
            prediction.requested_amount,
            prediction.risk_score,
            prediction.model_version,
            prediction.reason_codes,
            trace_id,
            scoring_event_id,
            source_event_id,
            scoring_source,
        )

        event = EventEnvelope(
            event_name=EVENT_CREDIT_SCORING_GENERATED,
            event_id=scoring_event_id,
            trace_id=trace_id,
            correlation_id=correlation_id_for(trace_id),
            causation_id=get_causation_id() if source_event_id is None else source_event_id,
            producer="scoring-service",
            payload=prediction.model_dump(mode="json"),
        )
        await enqueue_outbox_event(db, "scoring_outbox", event)
        return prediction

    async def handle_feature_event(self, event: EventEnvelope) -> bool:
        first_seen = False
        async with self._db.transaction() as tx:
            first_seen = await record_inbox_event(tx, "scoring_inbox", event)
            if first_seen:
                features = FeatureVector.model_validate(event.payload)
                await self.score_features(
                    features=features,
                    trace_id=event.trace_id,
                    db=tx,
                    source_event_id=event.event_id,
                )

        return first_seen

    async def flush_outbox(self) -> int:
        pending_events = await fetch_pending_outbox_events(
            self._db,
            "scoring_outbox",
            lease_seconds=self._settings.outbox_relay_claim_lease_seconds,
        )
        published = 0
        for claimed_event in pending_events:
            await self._broker.publish_event(claimed_event.event)
            await mark_outbox_event_published(
                self._db,
                "scoring_outbox",
                claimed_event.event.event_id,
                claim_token=claimed_event.claim_token,
            )
            published += 1
        return published
