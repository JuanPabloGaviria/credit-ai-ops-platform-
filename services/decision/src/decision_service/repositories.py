"""Persistence and outbox adapter for decision service."""

from __future__ import annotations

from uuid import uuid4

from contracts import (
    EVENT_CREDIT_DECISION_MADE,
    DecisionRequest,
    DecisionResult,
    EventEnvelope,
    ScorePrediction,
)
from shared_kernel import (
    DatabaseClient,
    DatabaseExecutor,
    RabbitMQClient,
    ServiceSettings,
    build_rabbitmq_client,
    correlation_id_for,
    decide_credit,
    enqueue_outbox_event,
    fetch_pending_outbox_events,
    get_causation_id,
    mark_outbox_event_published,
    record_inbox_event,
)


class DecisionRepository:
    """Decision repository with inbox/outbox semantics."""

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

    async def decide_from_score(
        self,
        score: ScorePrediction,
        trace_id: str,
        db: DatabaseExecutor | None = None,
    ) -> DecisionResult:
        if db is None:
            async with self._db.transaction() as tx:
                return await self.decide_from_score(score=score, trace_id=trace_id, db=tx)

        request = DecisionRequest(
            application_id=score.application_id,
            risk_score=score.risk_score,
            requested_amount=score.requested_amount,
            reason_codes=score.reason_codes,
        )

        decision = decide_credit(request)
        decision_event_id = str(uuid4())
        decision_id = str(uuid4())
        decision_source = (
            "manual_request" if score.model_version == "manual_request_v1" else "scoring_event"
        )
        await db.execute(
            """
            INSERT INTO credit_decision_history (
                decision_id,
                application_id,
                risk_score,
                decision,
                reason_codes,
                score_model_version,
                decision_source,
                trace_id,
                decision_event_id,
                decided_at,
                created_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW(), NOW())
            """,
            decision_id,
            decision.application_id,
            decision.risk_score,
            decision.decision,
            decision.reason_codes,
            score.model_version,
            decision_source,
            trace_id,
            decision_event_id,
        )

        event = EventEnvelope(
            event_name=EVENT_CREDIT_DECISION_MADE,
            event_id=decision_event_id,
            trace_id=trace_id,
            correlation_id=correlation_id_for(trace_id),
            causation_id=get_causation_id(),
            producer="decision-service",
            payload=decision.model_dump(mode="json"),
        )
        await enqueue_outbox_event(db, "decision_outbox", event)
        return decision

    async def handle_score_event(self, event: EventEnvelope) -> bool:
        first_seen = False
        async with self._db.transaction() as tx:
            first_seen = await record_inbox_event(tx, "decision_inbox", event)
            if first_seen:
                score = ScorePrediction.model_validate(event.payload)
                await self.decide_from_score(score=score, trace_id=event.trace_id, db=tx)

        return first_seen

    async def flush_outbox(self) -> int:
        pending_events = await fetch_pending_outbox_events(
            self._db,
            "decision_outbox",
            lease_seconds=self._settings.outbox_relay_claim_lease_seconds,
        )
        published = 0
        for claimed_event in pending_events:
            await self._broker.publish_event(claimed_event.event)
            await mark_outbox_event_published(
                self._db,
                "decision_outbox",
                claimed_event.event.event_id,
                claim_token=claimed_event.claim_token,
            )
            published += 1
        return published
