from __future__ import annotations

import asyncio
import logging
import os
from decimal import Decimal
from typing import cast
from uuid import uuid4

import pytest
from application_service.repositories import ApplicationRepository
from collab_assistant.repositories import AssistantRepository
from decision_service.repositories import DecisionRepository
from feature_service.repositories import FeatureRepository
from observability_audit.repositories import AuditRepository
from scoring_service.repositories import ScoringRepository

from contracts import (
    EVENT_CREDIT_APPLICATION_SUBMITTED,
    EVENT_CREDIT_ASSISTANT_SUMMARIZED,
    EVENT_CREDIT_DECISION_MADE,
    EVENT_CREDIT_FEATURE_MATERIALIZED,
    EVENT_CREDIT_SCORING_GENERATED,
    QUEUE_ASSISTANT_DECISION_MADE,
    QUEUE_AUDIT_CREDIT_EVENTS,
    QUEUE_DECISION_SCORING_GENERATED,
    QUEUE_FEATURE_APPLICATION_SUBMITTED,
    QUEUE_SCORING_FEATURE_MATERIALIZED,
    ROUTING_CREDIT_ALL,
    ApplicationInput,
)
from shared_kernel import (
    DatabaseClient,
    OutboxRelayConfig,
    OutboxRelayWorker,
    RabbitMQClient,
    build_rabbitmq_client,
    load_settings,
)
from tests.defaults import (
    TEST_MODEL_SIGNING_KEY_ID,
    TEST_MODEL_SIGNING_PRIVATE_KEY_PEM,
    TEST_MODEL_SIGNING_PUBLIC_KEY_PEM,
)
from tests.support.async_chain import (
    apply_all_migrations,
    integration_ready,
    truncate_domain_tables,
    wait_for_application_count,
    wait_for_trace_count,
)
from tests.support.mlops import seed_promoted_scoring_model

LOGGER = logging.getLogger(__name__)
os.environ.setdefault("MODEL_SIGNING_PRIVATE_KEY_PEM", TEST_MODEL_SIGNING_PRIVATE_KEY_PEM)
os.environ.setdefault("MODEL_SIGNING_PUBLIC_KEY_PEM", TEST_MODEL_SIGNING_PUBLIC_KEY_PEM)
os.environ.setdefault("MODEL_SIGNING_KEY_ID", TEST_MODEL_SIGNING_KEY_ID)
_OUTBOX_TABLE_BY_SERVICE: dict[str, str] = {
    "application": "application_outbox",
    "feature": "feature_outbox",
    "scoring": "scoring_outbox",
    "decision": "decision_outbox",
    "collab-assistant": "assistant_outbox",
}


async def _run_relay_until_stopped(
    worker: OutboxRelayWorker,
    *,
    stop_event: asyncio.Event,
    poll_interval_seconds: float,
) -> None:
    while not stop_event.is_set():
        _ = await worker.relay_once()
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_interval_seconds)
        except TimeoutError:
            continue


@pytest.mark.integration
def test_async_credit_chain_end_to_end() -> None:
    ready, reason = integration_ready()
    if not ready:
        pytest.skip(reason)

    async def scenario() -> None:
        LOGGER.info("starting_async_credit_chain_integration_test")
        settings_application = load_settings("application")
        settings_feature = load_settings("feature")
        settings_scoring = load_settings("scoring")
        settings_decision = load_settings("decision")
        settings_assistant = load_settings("collab-assistant")
        settings_audit = load_settings("observability-audit")

        application_id = f"app-{uuid4().hex[:12]}"
        applicant_id = f"applicant-{uuid4().hex[:12]}"
        trace_id = f"trace-{uuid4().hex}"

        db = DatabaseClient(settings_application.postgres_dsn)
        await db.connect()

        application_repository = ApplicationRepository(settings_application)
        feature_repository = FeatureRepository(settings_feature)
        scoring_repository = ScoringRepository(settings_scoring)
        decision_repository = DecisionRepository(settings_decision)
        assistant_repository = AssistantRepository(settings_assistant)
        audit_repository = AuditRepository(settings_audit.postgres_dsn)

        feature_consumer = build_rabbitmq_client(settings_feature)
        scoring_consumer = build_rabbitmq_client(settings_scoring)
        decision_consumer = build_rabbitmq_client(settings_decision)
        assistant_consumer = build_rabbitmq_client(settings_assistant)
        audit_consumer = build_rabbitmq_client(settings_audit)
        relay_stop_event = asyncio.Event()
        relay_tasks: list[asyncio.Task[None]] = []
        relay_databases: list[DatabaseClient] = []
        relay_brokers: list[RabbitMQClient] = []

        try:
            await apply_all_migrations(db)
            await truncate_domain_tables(db)
            await seed_promoted_scoring_model(
                settings_scoring=settings_scoring,
                trace_id=trace_id,
            )

            await application_repository.connect()
            await feature_repository.connect()
            await scoring_repository.connect()
            await decision_repository.connect()
            await assistant_repository.connect()
            await audit_repository.connect()

            await feature_consumer.connect()
            await scoring_consumer.connect()
            await decision_consumer.connect()
            await assistant_consumer.connect()
            await audit_consumer.connect()

            for service_name, settings in (
                ("application", settings_application),
                ("feature", settings_feature),
                ("scoring", settings_scoring),
                ("decision", settings_decision),
                ("collab-assistant", settings_assistant),
            ):
                relay_db = DatabaseClient(settings.postgres_dsn)
                relay_broker = build_rabbitmq_client(settings)
                await relay_db.connect()
                await relay_broker.connect()
                relay_databases.append(relay_db)
                relay_brokers.append(relay_broker)
                relay_worker = OutboxRelayWorker(
                    db=relay_db,
                    publish_event=relay_broker.publish_event,
                    config=OutboxRelayConfig(
                        outbox_table=_OUTBOX_TABLE_BY_SERVICE[service_name],
                        operation_prefix=f"{service_name}_outbox_relay",
                        batch_size=settings.outbox_relay_batch_size,
                        poll_interval_seconds=settings.outbox_relay_poll_interval_seconds,
                        claim_lease_seconds=settings.outbox_relay_claim_lease_seconds,
                        max_publish_attempts=settings.outbox_relay_max_publish_attempts,
                    ),
                )
                relay_tasks.append(
                    asyncio.create_task(
                        _run_relay_until_stopped(
                            relay_worker,
                            stop_event=relay_stop_event,
                            poll_interval_seconds=settings.outbox_relay_poll_interval_seconds,
                        )
                    )
                )

            await feature_consumer.consume(
                queue_name=QUEUE_FEATURE_APPLICATION_SUBMITTED,
                routing_keys=[EVENT_CREDIT_APPLICATION_SUBMITTED],
                handler=feature_repository.handle_submitted_event,
            )
            await scoring_consumer.consume(
                queue_name=QUEUE_SCORING_FEATURE_MATERIALIZED,
                routing_keys=[EVENT_CREDIT_FEATURE_MATERIALIZED],
                handler=scoring_repository.handle_feature_event,
            )
            await decision_consumer.consume(
                queue_name=QUEUE_DECISION_SCORING_GENERATED,
                routing_keys=[EVENT_CREDIT_SCORING_GENERATED],
                handler=decision_repository.handle_score_event,
            )
            await assistant_consumer.consume(
                queue_name=QUEUE_ASSISTANT_DECISION_MADE,
                routing_keys=[EVENT_CREDIT_DECISION_MADE],
                handler=assistant_repository.handle_decision_event,
            )
            await audit_consumer.consume(
                queue_name=QUEUE_AUDIT_CREDIT_EVENTS,
                routing_keys=[ROUTING_CREDIT_ALL],
                handler=audit_repository.handle_event,
            )

            application = ApplicationInput(
                application_id=application_id,
                applicant_id=applicant_id,
                monthly_income=5000.0,
                monthly_debt=1900.0,
                requested_amount=22000.0,
                credit_history_months=36,
                existing_defaults=0,
            )

            event_id = await application_repository.intake_application(
                application,
                trace_id=trace_id,
            )
            LOGGER.info("application_event_queued event_id=%s", event_id)

            await wait_for_application_count(
                db=db,
                table_name="application_submissions",
                application_id=application_id,
                expected_minimum=1,
                label="application_submission_recorded",
            )
            await wait_for_application_count(
                db=db,
                table_name="feature_vectors",
                application_id=application_id,
                expected_minimum=1,
                label="feature_vectors_materialized",
            )
            await wait_for_application_count(
                db=db,
                table_name="feature_vector_history",
                application_id=application_id,
                expected_minimum=1,
                label="feature_vector_history_recorded",
            )
            await wait_for_application_count(
                db=db,
                table_name="score_predictions",
                application_id=application_id,
                expected_minimum=1,
                label="score_predictions_generated",
            )
            await wait_for_application_count(
                db=db,
                table_name="score_prediction_history",
                application_id=application_id,
                expected_minimum=1,
                label="score_prediction_history_recorded",
            )
            await wait_for_application_count(
                db=db,
                table_name="credit_decisions",
                application_id=application_id,
                expected_minimum=1,
                label="credit_decision_made",
            )
            await wait_for_application_count(
                db=db,
                table_name="credit_decision_history",
                application_id=application_id,
                expected_minimum=1,
                label="credit_decision_history_recorded",
            )
            await wait_for_application_count(
                db=db,
                table_name="assistant_summaries",
                application_id=application_id,
                expected_minimum=1,
                label="assistant_summary_generated",
            )
            await wait_for_application_count(
                db=db,
                table_name="assistant_summary_history",
                application_id=application_id,
                expected_minimum=1,
                label="assistant_summary_history_recorded",
            )
            await wait_for_trace_count(
                db=db,
                table_name="audit_events",
                trace_id=trace_id,
                expected_minimum=5,
                label="audit_events_recorded",
            )

            decision_row = await db.fetchrow(
                """
                SELECT risk_score, decision, reason_codes
                FROM credit_decisions
                WHERE application_id = $1
                """,
                application_id,
            )
            assert decision_row is not None, "decision row must exist after async chain execution"
            raw_risk_score = decision_row["risk_score"]
            assert isinstance(raw_risk_score, int | float | Decimal | str)
            decision_risk_score = float(raw_risk_score)
            assert 0 <= decision_risk_score <= 1
            decision_value = cast(str, decision_row["decision"])
            assert decision_value in {"approve", "review", "decline"}
            reason_codes = cast(list[str], decision_row["reason_codes"])
            assert reason_codes, "decision reason codes must be populated"

            decision_history_row = await db.fetchrow(
                """
                SELECT score_model_version, decision_source, decision_event_id
                FROM credit_decision_history
                WHERE application_id = $1
                ORDER BY decided_at DESC
                LIMIT 1
                """,
                application_id,
            )
            assert decision_history_row is not None, "decision history row must exist"
            assert cast(str, decision_history_row["score_model_version"])
            assert cast(str, decision_history_row["decision_source"]) == "scoring_event"
            assert cast(str, decision_history_row["decision_event_id"])

            feature_history_row = await db.fetchrow(
                """
                SELECT materialization_source, feature_event_id, source_event_id
                FROM feature_vector_history
                WHERE application_id = $1
                ORDER BY materialized_at DESC
                LIMIT 1
                """,
                application_id,
            )
            assert feature_history_row is not None, "feature history row must exist"
            assert cast(str, feature_history_row["materialization_source"]) == "application_event"
            feature_event_id = cast(str, feature_history_row["feature_event_id"])
            assert feature_event_id
            assert cast(str, feature_history_row["source_event_id"]) == event_id

            score_history_row = await db.fetchrow(
                """
                SELECT scoring_source, scoring_event_id, source_event_id
                FROM score_prediction_history
                WHERE application_id = $1
                ORDER BY scored_at DESC
                LIMIT 1
                """,
                application_id,
            )
            assert score_history_row is not None, "score history row must exist"
            assert cast(str, score_history_row["scoring_source"]) == "feature_event"
            assert cast(str, score_history_row["scoring_event_id"])
            assert cast(str, score_history_row["source_event_id"]) == feature_event_id

            audit_rows = await db.fetch(
                """
                SELECT event_id, event_name, correlation_id, causation_id
                FROM audit_events
                WHERE trace_id = $1
                """,
                trace_id,
            )
            event_rows = {cast(str, row["event_name"]): row for row in audit_rows}
            event_names = set(event_rows)
            assert {
                EVENT_CREDIT_APPLICATION_SUBMITTED,
                EVENT_CREDIT_FEATURE_MATERIALIZED,
                EVENT_CREDIT_SCORING_GENERATED,
                EVENT_CREDIT_DECISION_MADE,
                EVENT_CREDIT_ASSISTANT_SUMMARIZED,
            }.issubset(event_names)
            assert all(cast(str, row["correlation_id"]) == trace_id for row in audit_rows)
            assert event_rows[EVENT_CREDIT_APPLICATION_SUBMITTED]["causation_id"] is None
            assert (
                cast(str, event_rows[EVENT_CREDIT_FEATURE_MATERIALIZED]["causation_id"]) == event_id
            )
            assert (
                cast(str, event_rows[EVENT_CREDIT_SCORING_GENERATED]["causation_id"])
                == cast(str, score_history_row["source_event_id"])
            )
            assert (
                cast(str, event_rows[EVENT_CREDIT_DECISION_MADE]["causation_id"])
                == cast(str, score_history_row["scoring_event_id"])
            )
            assert (
                cast(str, event_rows[EVENT_CREDIT_ASSISTANT_SUMMARIZED]["causation_id"])
                == cast(str, decision_history_row["decision_event_id"])
            )
        finally:
            relay_stop_event.set()
            if relay_tasks:
                await asyncio.gather(*relay_tasks, return_exceptions=True)
            while relay_brokers:
                relay_broker = relay_brokers.pop()
                await relay_broker.close()
            while relay_databases:
                relay_db = relay_databases.pop()
                await relay_db.close()

            await audit_consumer.close()
            await assistant_consumer.close()
            await decision_consumer.close()
            await scoring_consumer.close()
            await feature_consumer.close()

            await audit_repository.close()
            await assistant_repository.close()
            await decision_repository.close()
            await scoring_repository.close()
            await feature_repository.close()
            await application_repository.close()
            await db.close()

    asyncio.run(scenario())
