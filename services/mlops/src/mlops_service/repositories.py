"""Persistence and outbox adapter for MLOps training lifecycle."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from contracts import (
    EVENT_CREDIT_MODEL_PROMOTED,
    EvaluateRunResponse,
    EventEnvelope,
    MLOpsRunResponse,
    ModelMetadata,
    PromoteModelRequest,
    PromoteModelResponse,
    RegisterModelRequest,
    RegisterModelResponse,
    TrainingMetrics,
    TrainRunResponse,
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
)

_ML_OUTBOX_TABLE = "mlops_outbox"
_ML_REGISTRY_STATUS_CANDIDATE = "candidate"
_INSERT_SINGLE_ROW_TAG = "INSERT 0 1"


class MLOpsRepository:
    """MLOps persistence adapter for runs, registry, and promotion events."""

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

    async def persist_training_run(
        self,
        *,
        run: TrainRunResponse,
        dataset_reference: str,
    ) -> None:
        insert_result = await self._db.execute(
            """
            INSERT INTO ml_training_runs (
                run_id,
                model_name,
                dataset_reference,
                dataset_hash,
                random_seed,
                algorithm,
                feature_spec_ref,
                training_spec_ref,
                training_metrics,
                artifact_uri,
                artifact_digest,
                status,
                created_at,
                completed_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10, $11, $12, NOW(), NOW())
            ON CONFLICT (run_id) DO NOTHING
            """,
            run.run_id,
            run.model_name,
            dataset_reference,
            run.dataset_hash,
            run.random_seed,
            run.algorithm,
            run.feature_spec_ref,
            run.training_spec_ref,
            json.dumps(run.metrics.model_dump(mode="json")),
            run.artifact_uri,
            run.artifact_digest,
            "succeeded",
        )
        if insert_result != _INSERT_SINGLE_ROW_TAG:
            raise ServiceError(
                error_code="ML_RUN_ALREADY_EXISTS",
                message="Training run already exists and cannot be overwritten",
                operation="mlops_persist_training_run",
                status_code=409,
                cause=run.run_id,
            )

    async def load_training_run(self, run_id: str) -> MLOpsRunResponse:
        row = await self._db.fetchrow(
            """
            SELECT
                run_id,
                model_name,
                dataset_hash,
                random_seed,
                algorithm,
                feature_spec_ref,
                training_spec_ref,
                artifact_uri,
                artifact_digest,
                status,
                training_metrics,
                created_at,
                completed_at
            FROM ml_training_runs
            WHERE run_id = $1
            """,
            run_id,
        )
        if row is None:
            raise ServiceError(
                error_code="ML_RUN_NOT_FOUND",
                message="Training run was not found",
                operation="mlops_load_training_run",
                status_code=404,
                cause=run_id,
            )
        return MLOpsRunResponse(
            run_id=cast(str, row["run_id"]),
            model_name=cast(str, row["model_name"]),
            dataset_hash=cast(str, row["dataset_hash"]),
            random_seed=cast(int, row["random_seed"]),
            algorithm=cast(str, row["algorithm"]),
            feature_spec_ref=cast(str, row["feature_spec_ref"]),
            training_spec_ref=cast(str, row["training_spec_ref"]),
            artifact_uri=cast(str, row["artifact_uri"]),
            artifact_digest=cast(str, row["artifact_digest"]),
            status=cast(str, row["status"]),
            metrics=_load_training_metrics_payload(
                row["training_metrics"],
                operation="mlops_load_training_run",
            ),
            created_at=cast(datetime, row["created_at"]),
            completed_at=cast(datetime, row["completed_at"]),
        )

    async def persist_evaluation_run(
        self,
        *,
        run_id: str,
        metrics: TrainingMetrics,
        passed_policy: bool,
        policy_failures: list[str],
    ) -> EvaluateRunResponse:
        evaluation_id = f"eval-{uuid4().hex[:20]}"
        await self._db.execute(
            """
            INSERT INTO ml_evaluation_runs (
                evaluation_id,
                run_id,
                evaluation_metrics,
                passed_policy,
                policy_failures,
                created_at
            ) VALUES ($1, $2, $3::jsonb, $4, $5::text[], NOW())
            """,
            evaluation_id,
            run_id,
            json.dumps(metrics.model_dump(mode="json")),
            passed_policy,
            policy_failures,
        )
        return EvaluateRunResponse(
            evaluation_id=evaluation_id,
            run_id=run_id,
            metrics=metrics,
            passed_policy=passed_policy,
            policy_failures=policy_failures,
        )

    async def load_evaluation_run(self, evaluation_id: str) -> EvaluateRunResponse:
        row = await self._db.fetchrow(
            """
            SELECT
                evaluation_id,
                run_id,
                evaluation_metrics,
                passed_policy,
                policy_failures
            FROM ml_evaluation_runs
            WHERE evaluation_id = $1
            """,
            evaluation_id,
        )
        if row is None:
            raise ServiceError(
                error_code="ML_EVALUATION_NOT_FOUND",
                message="Evaluation run was not found",
                operation="mlops_load_evaluation_run",
                status_code=404,
                cause=evaluation_id,
            )
        raw_failures = cast(list[str], row["policy_failures"])
        return EvaluateRunResponse(
            evaluation_id=cast(str, row["evaluation_id"]),
            run_id=cast(str, row["run_id"]),
            metrics=_load_training_metrics_payload(
                row["evaluation_metrics"],
                operation="mlops_load_evaluation_run",
            ),
            passed_policy=cast(bool, row["passed_policy"]),
            policy_failures=raw_failures,
        )

    async def register_model_candidate(
        self,
        *,
        request: RegisterModelRequest,
        run: MLOpsRunResponse,
        evaluation: EvaluateRunResponse,
        metadata: ModelMetadata,
        signed_artifact_uri: str,
        signed_artifact_digest: str,
        signature_algorithm: str,
        signature_key_id: str,
        artifact_signature: str,
        model_card_uri: str,
        model_card_checksum: str,
        environment_snapshot: dict[str, str],
    ) -> RegisterModelResponse:
        insert_result = await self._db.execute(
            """
            INSERT INTO model_registry (
                model_name,
                model_version,
                dataset_hash,
                random_seed,
                environment_fingerprint,
                run_id,
                evaluation_id,
                feature_spec_ref,
                training_spec_ref,
                algorithm,
                training_metrics,
                evaluation_metrics,
                artifact_uri,
                artifact_digest,
                signature_algorithm,
                signature_key_id,
                artifact_signature,
                model_card_uri,
                model_card_checksum,
                environment_snapshot,
                status,
                stage,
                promoted_at,
                updated_at,
                created_at
            ) VALUES (
                $1, $2, $3, $4, $5,
                $6, $7, $8, $9, $10,
                $11::jsonb, $12::jsonb, $13, $14, $15, $16, $17,
                $18, $19, $20::jsonb, $21, NULL, NULL, NOW(), NOW()
            )
            ON CONFLICT (model_name, model_version) DO NOTHING
            """,
            request.model_name,
            request.model_version,
            run.dataset_hash,
            run.random_seed,
            metadata.environment_fingerprint,
            request.run_id,
            request.evaluation_id,
            request.feature_spec_ref,
            request.training_spec_ref,
            run.algorithm,
            json.dumps(run.metrics.model_dump(mode="json")),
            json.dumps(evaluation.metrics.model_dump(mode="json")),
            signed_artifact_uri,
            signed_artifact_digest,
            signature_algorithm,
            signature_key_id,
            artifact_signature,
            model_card_uri,
            model_card_checksum,
            json.dumps(environment_snapshot),
            _ML_REGISTRY_STATUS_CANDIDATE,
        )
        if insert_result != _INSERT_SINGLE_ROW_TAG:
            raise ServiceError(
                error_code="MODEL_VERSION_ALREADY_EXISTS",
                message="Model version already exists and registry records are immutable",
                operation="mlops_register_model_candidate",
                status_code=409,
                cause=f"{request.model_name}:{request.model_version}",
            )

        return RegisterModelResponse(
            model_name=request.model_name,
            model_version=request.model_version,
            status=_ML_REGISTRY_STATUS_CANDIDATE,
            model_card_uri=model_card_uri,
            metadata=metadata,
        )

    async def promote_model(
        self,
        *,
        request: PromoteModelRequest,
        trace_id: str,
        db: DatabaseExecutor | None = None,
    ) -> PromoteModelResponse:
        if db is None:
            async with self._db.transaction() as tx:
                return await self.promote_model(
                    request=request,
                    trace_id=trace_id,
                    db=tx,
                )

        promoted_at = datetime.now(UTC)
        event_id = str(uuid4())
        insert_result = await db.execute(
            """
            INSERT INTO model_stage_assignments (
                event_id,
                model_name,
                model_version,
                stage,
                approved_by,
                approval_ticket,
                risk_signoff_ref,
                promoted_at,
                created_at
            )
            SELECT
                $3,
                model_name,
                model_version,
                $4,
                $5,
                $6,
                $7,
                $8,
                NOW()
            FROM model_registry
            WHERE model_name = $1
              AND model_version = $2
            """,
            request.model_name,
            request.model_version,
            event_id,
            request.stage,
            request.approved_by,
            request.approval_ticket,
            request.risk_signoff_ref,
            promoted_at,
        )
        if insert_result != _INSERT_SINGLE_ROW_TAG:
            raise ServiceError(
                error_code="MODEL_VERSION_NOT_FOUND",
                message="Model candidate not found for promotion",
                operation="mlops_promote_model",
                status_code=404,
                cause=f"{request.model_name}:{request.model_version}",
            )

        event = EventEnvelope(
            event_name=EVENT_CREDIT_MODEL_PROMOTED,
            event_id=event_id,
            trace_id=trace_id,
            correlation_id=correlation_id_for(trace_id),
            causation_id=get_causation_id(),
            producer="mlops-service",
            payload={
                "model_name": request.model_name,
                "model_version": request.model_version,
                "stage": request.stage,
                "approved_by": request.approved_by,
                "approval_ticket": request.approval_ticket,
                "risk_signoff_ref": request.risk_signoff_ref,
                "promoted_at": promoted_at.isoformat(),
            },
        )
        await enqueue_outbox_event(db, _ML_OUTBOX_TABLE, event)
        return PromoteModelResponse(
            model_name=request.model_name,
            model_version=request.model_version,
            stage=request.stage,
            promoted_at=promoted_at,
            event_id=event_id,
        )

    async def flush_outbox(self) -> int:
        pending_events = await fetch_pending_outbox_events(
            self._db,
            _ML_OUTBOX_TABLE,
            lease_seconds=self._settings.outbox_relay_claim_lease_seconds,
        )
        published = 0
        for claimed_event in pending_events:
            await self._broker.publish_event(claimed_event.event)
            await mark_outbox_event_published(
                self._db,
                _ML_OUTBOX_TABLE,
                claimed_event.event.event_id,
                claim_token=claimed_event.claim_token,
            )
            published += 1
        return published


def _load_training_metrics_payload(
    raw_payload: object,
    *,
    operation: str,
) -> TrainingMetrics:
    if isinstance(raw_payload, str):
        try:
            decoded_payload = json.loads(raw_payload)
        except json.JSONDecodeError as exc:
            raise ServiceError(
                error_code="ML_METRICS_PAYLOAD_INVALID",
                message="Stored ML metrics payload is not valid JSON",
                operation=operation,
                status_code=500,
                cause=str(exc),
            ) from exc
    else:
        decoded_payload = raw_payload
    return TrainingMetrics.model_validate(decoded_payload)
