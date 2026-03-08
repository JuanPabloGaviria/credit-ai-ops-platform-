from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from typing import Any
from uuid import uuid4

import api_gateway.routes as gateway_routes
import pytest
from api_gateway.idempotency import IdempotencyReservation
from api_gateway.main import app as gateway_app
from application_service.repositories import ApplicationRepository
from decision_service.repositories import DecisionRepository
from fastapi.testclient import TestClient
from feature_service.repositories import FeatureRepository
from observability_audit.repositories import AuditRepository
from scoring_service.repositories import ScoringRepository

from contracts import (
    EVENT_CREDIT_APPLICATION_SUBMITTED,
    EVENT_CREDIT_FEATURE_MATERIALIZED,
    EVENT_CREDIT_SCORING_GENERATED,
    QUEUE_AUDIT_CREDIT_EVENTS,
    QUEUE_DECISION_SCORING_GENERATED,
    QUEUE_FEATURE_APPLICATION_SUBMITTED,
    QUEUE_SCORING_FEATURE_MATERIALIZED,
    ROUTING_CREDIT_ALL,
    ApplicationInput,
    DecisionResult,
    FeatureVector,
    GatewayCreditEvaluationResponse,
    ScorePrediction,
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
}


class _PerfIdempotencyRepository:
    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def reserve_request(
        self,
        *,
        idempotency_key: str,
        endpoint: str,
        request_hash: str,
    ) -> IdempotencyReservation:
        _ = idempotency_key
        _ = endpoint
        _ = request_hash
        return IdempotencyReservation(replay_payload=None)

    async def persist_response(
        self,
        *,
        idempotency_key: str,
        response_payload: dict[str, Any],
        response_status_code: int,
    ) -> None:
        _ = idempotency_key
        _ = response_payload
        _ = response_status_code

    async def persist_failure(
        self,
        *,
        idempotency_key: str,
        error_payload: dict[str, Any],
        error_status_code: int,
    ) -> None:
        _ = idempotency_key
        _ = error_payload
        _ = error_status_code


def _install_perf_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    class _RepositoryFactory:
        @classmethod
        def from_dsn(
            cls,
            postgres_dsn: str,
            *,
            ttl_seconds: int | None = None,
            stale_after_seconds: int | None = None,
        ) -> _PerfIdempotencyRepository:
            _ = cls
            _ = postgres_dsn
            _ = ttl_seconds
            _ = stale_after_seconds
            return _PerfIdempotencyRepository()

    monkeypatch.setattr(gateway_routes, "GatewayIdempotencyRepository", _RepositoryFactory)


def _install_perf_evaluation(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _fake_build_credit_evaluation(
        application: Any,
        *,
        settings: Any,
    ) -> GatewayCreditEvaluationResponse:
        payload = dict(application.model_dump(mode="json"))
        return GatewayCreditEvaluationResponse(
            features=FeatureVector(
                application_id=str(payload["application_id"]),
                requested_amount=float(payload["requested_amount"]),
                debt_to_income=0.36,
                amount_to_income=0.3333,
                credit_history_months=int(payload["credit_history_months"]),
                existing_defaults=int(payload["existing_defaults"]),
            ),
            score=ScorePrediction(
                application_id=str(payload["application_id"]),
                requested_amount=float(payload["requested_amount"]),
                risk_score=0.31,
                model_version="baseline_lr_v1",
                reason_codes=["LOW_RISK_PROFILE"],
            ),
            decision=DecisionResult(
                application_id=str(payload["application_id"]),
                risk_score=0.31,
                decision="approve",
                reason_codes=["LOW_RISK_PROFILE", "POLICY_AUTO_APPROVE"],
            ),
        )

    monkeypatch.setattr(gateway_routes, "_build_credit_evaluation", _fake_build_credit_evaluation)


def _p95_milliseconds(samples: list[float]) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return ordered[index] * 1000.0


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


@pytest.mark.smoke
def test_gateway_local_latency_smoke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_perf_repository(monkeypatch)
    _install_perf_evaluation(monkeypatch)
    client = TestClient(gateway_app)
    request_count = 100
    durations: list[float] = []
    error_count = 0

    httpx_logger = logging.getLogger("httpx")
    previous_httpx_level = httpx_logger.level
    httpx_logger.setLevel(logging.WARNING)
    try:
        for i in range(request_count):
            started = time.perf_counter()
            response = client.post(
                "/v1/gateway/credit-evaluate",
                json={
                    "application_id": f"app-{i:08d}",
                    "applicant_id": f"applicant-{i:08d}",
                    "monthly_income": 5000.0,
                    "monthly_debt": 1800.0,
                    "requested_amount": 20000.0,
                    "credit_history_months": 36,
                    "existing_defaults": 0,
                },
                headers={"x-idempotency-key": f"idem-perf-{i:08d}"},
            )
            durations.append(time.perf_counter() - started)
            if response.status_code >= 500:
                error_count += 1
    finally:
        httpx_logger.setLevel(previous_httpx_level)

    p95_ms = _p95_milliseconds(durations)
    error_rate = error_count / request_count
    LOGGER.info(
        "gateway_local_smoke_metrics p95_ms=%.2f error_rate=%.4f request_count=%d",
        p95_ms,
        error_rate,
        request_count,
    )

    assert p95_ms <= 300.0, (
        f"Gateway local smoke threshold breach: p95_ms={p95_ms:.2f}, target<=300.0"
    )
    assert error_rate < 0.01, (
        f"Gateway local smoke error threshold breach: error_rate={error_rate:.4f}, target<0.01"
    )


@pytest.mark.perf
def test_async_processing_delay_baseline() -> None:
    ready, reason = integration_ready()
    if not ready:
        pytest.skip(reason)

    async def scenario() -> None:
        settings_application = load_settings("application")
        settings_feature = load_settings("feature")
        settings_scoring = load_settings("scoring")
        settings_decision = load_settings("decision")
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
        audit_repository = AuditRepository(settings_audit.postgres_dsn)

        feature_consumer = build_rabbitmq_client(settings_feature)
        scoring_consumer = build_rabbitmq_client(settings_scoring)
        decision_consumer = build_rabbitmq_client(settings_decision)
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
            await audit_repository.connect()

            await feature_consumer.connect()
            await scoring_consumer.connect()
            await decision_consumer.connect()
            await audit_consumer.connect()

            for service_name, settings in (
                ("application", settings_application),
                ("feature", settings_feature),
                ("scoring", settings_scoring),
                ("decision", settings_decision),
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

            _ = await application_repository.intake_application(application, trace_id=trace_id)
            started = time.perf_counter()

            await wait_for_application_count(
                db=db,
                table_name="credit_decisions",
                application_id=application_id,
                expected_minimum=1,
                label="decision_delay_measurement",
                timeout_seconds=10.0,
            )
            delay_seconds = time.perf_counter() - started

            await wait_for_trace_count(
                db=db,
                table_name="audit_events",
                trace_id=trace_id,
                expected_minimum=4,
                label="audit_chain_completion",
                timeout_seconds=10.0,
            )

            LOGGER.info(
                "async_perf_metrics delay_seconds=%.3f target_seconds=2.0",
                delay_seconds,
            )
            assert delay_seconds <= 2.0, (
                f"Async processing SLO breach: delay_seconds={delay_seconds:.3f}, target<=2.0"
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
            await decision_consumer.close()
            await scoring_consumer.close()
            await feature_consumer.close()

            await audit_repository.close()
            await decision_repository.close()
            await scoring_repository.close()
            await feature_repository.close()
            await application_repository.close()
            await db.close()

    asyncio.run(scenario())
