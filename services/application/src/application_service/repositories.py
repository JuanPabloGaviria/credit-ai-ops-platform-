"""Persistence and outbox adapter for application service."""

from __future__ import annotations

from uuid import uuid4

from contracts import (
    EVENT_CREDIT_APPLICATION_SUBMITTED,
    ApplicationInput,
    EventEnvelope,
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
)


class ApplicationRepository:
    """Application write adapter with outbox publish support."""

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

    async def intake_application(
        self,
        application: ApplicationInput,
        trace_id: str,
        db: DatabaseExecutor | None = None,
    ) -> str:
        if db is None:
            async with self._db.transaction() as tx:
                return await self.intake_application(
                    application=application,
                    trace_id=trace_id,
                    db=tx,
                )

        event_id = str(uuid4())
        submission_id = str(uuid4())
        await db.execute(
            """
            INSERT INTO application_submissions (
                submission_id,
                application_id,
                applicant_id,
                monthly_income,
                monthly_debt,
                requested_amount,
                credit_history_months,
                existing_defaults,
                trace_id,
                intake_event_id,
                submitted_at,
                created_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, NOW(), NOW())
            """,
            submission_id,
            application.application_id,
            application.applicant_id,
            application.monthly_income,
            application.monthly_debt,
            application.requested_amount,
            application.credit_history_months,
            application.existing_defaults,
            trace_id,
            event_id,
        )

        event = EventEnvelope(
            event_name=EVENT_CREDIT_APPLICATION_SUBMITTED,
            event_id=event_id,
            trace_id=trace_id,
            correlation_id=correlation_id_for(trace_id),
            causation_id=get_causation_id(),
            producer="application-service",
            payload=application.model_dump(mode="json"),
        )
        await enqueue_outbox_event(db, "application_outbox", event)
        return event.event_id

    async def flush_outbox(self) -> int:
        pending_events = await fetch_pending_outbox_events(
            self._db,
            "application_outbox",
            lease_seconds=self._settings.outbox_relay_claim_lease_seconds,
        )
        published = 0
        for claimed_event in pending_events:
            await self._broker.publish_event(claimed_event.event)
            await mark_outbox_event_published(
                self._db,
                "application_outbox",
                claimed_event.event.event_id,
                claim_token=claimed_event.claim_token,
            )
            published += 1
        return published
