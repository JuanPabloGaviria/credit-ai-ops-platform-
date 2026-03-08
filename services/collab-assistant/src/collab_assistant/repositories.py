"""Persistence and async consumer adapter for collaborator assistant."""

from __future__ import annotations

from typing import cast
from uuid import uuid4

from contracts import (
    EVENT_CREDIT_ASSISTANT_SUMMARIZED,
    AssistantSummaryRequest,
    AssistantSummaryResponse,
    DecisionResult,
    EventEnvelope,
)
from shared_kernel import (
    DatabaseClient,
    DatabaseExecutor,
    RabbitMQClient,
    ServiceError,
    ServiceSettings,
    build_rabbitmq_client,
    correlation_id_for,
    enqueue_outbox_event,
    fetch_pending_outbox_events,
    get_causation_id,
    mark_outbox_event_published,
    record_inbox_event,
    summarize_case,
)


class AssistantRepository:
    """Repository with deterministic summary generation and inbox/outbox semantics."""

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

    async def summarize_request(
        self,
        request: AssistantSummaryRequest,
        trace_id: str,
        db: DatabaseExecutor | None = None,
        *,
        source_event_id: str | None = None,
    ) -> AssistantSummaryResponse:
        if db is None:
            async with self._db.transaction() as tx:
                return await self.summarize_request(
                    request=request,
                    trace_id=trace_id,
                    db=tx,
                    source_event_id=source_event_id,
                )

        response = summarize_case(request)
        summary_event_id = str(uuid4())
        summary_source = "decision_event" if source_event_id is not None else "manual_request"
        await db.execute(
            """
            INSERT INTO assistant_summary_history (
                summary_id,
                application_id,
                mode,
                summary,
                decision,
                risk_score,
                reason_codes,
                trace_id,
                source_event_id,
                summary_event_id,
                summary_source,
                summarized_at,
                created_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, NOW(), NOW())
            """,
            str(uuid4()),
            request.application_id,
            response.mode,
            response.summary,
            request.decision,
            request.risk_score,
            request.reason_codes,
            trace_id,
            source_event_id,
            summary_event_id,
            summary_source,
        )

        event = EventEnvelope(
            event_name=EVENT_CREDIT_ASSISTANT_SUMMARIZED,
            event_id=summary_event_id,
            trace_id=trace_id,
            correlation_id=correlation_id_for(trace_id),
            causation_id=get_causation_id() if source_event_id is None else source_event_id,
            producer="collab-assistant-service",
            payload={
                "application_id": response.application_id,
                "mode": response.mode,
                "summary": response.summary,
                "decision": request.decision,
                "risk_score": request.risk_score,
                "reason_codes": request.reason_codes,
            },
        )
        await enqueue_outbox_event(db, "assistant_outbox", event)
        return response

    async def get_summary(self, application_id: str) -> AssistantSummaryResponse:
        row = await self._db.fetchrow(
            """
            SELECT application_id, mode, summary
            FROM assistant_summaries
            WHERE application_id = $1
            """,
            application_id,
        )
        if row is None:
            raise ServiceError(
                error_code="ASSISTANT_SUMMARY_NOT_FOUND",
                message="No assistant summary exists for the requested application",
                operation="assistant_get_summary",
                status_code=404,
                cause=application_id,
                hint="Generate a summary first or confirm application_id",
            )

        return AssistantSummaryResponse(
            application_id=cast(str, row["application_id"]),
            mode=cast(str, row["mode"]),
            summary=cast(str, row["summary"]),
        )

    async def handle_decision_event(self, event: EventEnvelope) -> bool:
        first_seen = False
        async with self._db.transaction() as tx:
            first_seen = await record_inbox_event(tx, "assistant_inbox", event)
            if first_seen:
                decision = DecisionResult.model_validate(event.payload)
                await self.summarize_request(
                    request=AssistantSummaryRequest(
                        application_id=decision.application_id,
                        decision=decision.decision,
                        risk_score=decision.risk_score,
                        reason_codes=decision.reason_codes,
                    ),
                    trace_id=event.trace_id,
                    db=tx,
                    source_event_id=event.event_id,
                )

        return first_seen

    async def flush_outbox(self) -> int:
        pending_events = await fetch_pending_outbox_events(
            self._db,
            "assistant_outbox",
            lease_seconds=self._settings.outbox_relay_claim_lease_seconds,
        )
        published = 0
        for claimed_event in pending_events:
            await self._broker.publish_event(claimed_event.event)
            await mark_outbox_event_published(
                self._db,
                "assistant_outbox",
                claimed_event.event.event_id,
                claim_token=claimed_event.claim_token,
            )
            published += 1
        return published
