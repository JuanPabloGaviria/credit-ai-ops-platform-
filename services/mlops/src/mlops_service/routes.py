"""mlops service API routes."""

from __future__ import annotations

from fastapi import APIRouter, Header

from contracts import (
    EvaluateRunRequest,
    EvaluateRunResponse,
    MLOpsRunResponse,
    PromoteModelRequest,
    PromoteModelResponse,
    RegisterModelRequest,
    RegisterModelResponse,
    TrainRunRequest,
    TrainRunResponse,
)
from shared_kernel import (
    ArtifactStore,
    ArtifactStoreError,
    ServiceError,
    ServiceSettings,
    authorize_request,
    build_artifact_store,
    get_trace_id,
    load_settings,
    normalize_optional_idempotency_key,
    require_idempotency_key,
)

from .lifecycle import (
    build_environment_snapshot,
    build_registry_metadata,
    build_signed_registered_model_artifact,
    evaluate_policy,
    train_deterministic_model,
    write_model_card,
)
from .repositories import MLOpsRepository

router = APIRouter(prefix="/v1", tags=["mlops"])


@router.get("/mlops/status")
async def service_status(
    x_idempotency_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    _ = normalize_optional_idempotency_key(
        x_idempotency_key,
        operation="mlops_status",
    )
    settings = load_settings("mlops")
    await authorize_request(
        settings=settings,
        authorization=authorization,
        operation="mlops_status",
    )
    return {"service": "mlops", "status": "operational"}


@router.post("/mlops/train")
async def train_run(
    request: TrainRunRequest,
    x_idempotency_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> TrainRunResponse:
    _ = require_idempotency_key(
        x_idempotency_key,
        operation="mlops_train",
    )
    settings = load_settings("mlops")
    await authorize_request(
        settings=settings,
        authorization=authorization,
        operation="mlops_train",
    )
    artifact_store = _build_mlops_artifact_store(settings)
    repository = MLOpsRepository(settings)
    await repository.connect()
    try:
        train_response = train_deterministic_model(
            request,
            artifact_store=artifact_store,
        )
        await repository.persist_training_run(
            run=train_response,
            dataset_reference=request.dataset_reference,
        )
        return train_response
    finally:
        await repository.close()


@router.post("/mlops/evaluate")
async def evaluate_run(
    request: EvaluateRunRequest,
    x_idempotency_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> EvaluateRunResponse:
    _ = require_idempotency_key(
        x_idempotency_key,
        operation="mlops_evaluate",
    )
    settings = load_settings("mlops")
    await authorize_request(
        settings=settings,
        authorization=authorization,
        operation="mlops_evaluate",
    )
    repository = MLOpsRepository(settings)
    await repository.connect()
    try:
        training_run = await repository.load_training_run(request.run_id)
        policy = evaluate_policy(
            metrics=training_run.metrics,
            min_auc=settings.mlops_min_auc,
            max_calibration_error=settings.mlops_max_calibration_error,
        )
        return await repository.persist_evaluation_run(
            run_id=request.run_id,
            metrics=training_run.metrics,
            passed_policy=policy.passed,
            policy_failures=policy.failures,
        )
    finally:
        await repository.close()


@router.post("/mlops/register")
async def register_model(
    request: RegisterModelRequest,
    x_idempotency_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> RegisterModelResponse:
    _ = require_idempotency_key(
        x_idempotency_key,
        operation="mlops_register",
    )
    settings = load_settings("mlops")
    await authorize_request(
        settings=settings,
        authorization=authorization,
        operation="mlops_register",
    )
    artifact_store = _build_mlops_artifact_store(settings)
    repository = MLOpsRepository(settings)
    await repository.connect()
    try:
        run = await repository.load_training_run(request.run_id)
        evaluation = await repository.load_evaluation_run(request.evaluation_id)
        if evaluation.run_id != request.run_id:
            raise ServiceError(
                error_code="ML_RUN_EVALUATION_MISMATCH",
                message="Evaluation run does not belong to the provided training run",
                operation="mlops_register",
                status_code=400,
                hint="Pass matching run_id and evaluation_id values",
            )
        if request.feature_spec_ref != run.feature_spec_ref:
            raise ServiceError(
                error_code="ML_FEATURE_SPEC_MISMATCH",
                message="Registration request feature spec does not match the training run",
                operation="mlops_register",
                status_code=400,
                hint="Use the exact feature_spec_ref captured during the training run",
            )
        if request.training_spec_ref != run.training_spec_ref:
            raise ServiceError(
                error_code="ML_TRAINING_SPEC_MISMATCH",
                message="Registration request training spec does not match the training run",
                operation="mlops_register",
                status_code=400,
                hint="Use the exact training_spec_ref captured during the training run",
            )
        if not evaluation.passed_policy:
            raise ServiceError(
                error_code="ML_POLICY_GATE_FAILED",
                message="Model failed evaluation policy gate and cannot be registered",
                operation="mlops_register",
                status_code=409,
                cause=",".join(evaluation.policy_failures),
            )

        environment_snapshot = build_environment_snapshot()
        metadata = build_registry_metadata(
            request,
            dataset_hash=run.dataset_hash,
            random_seed=run.random_seed,
            environment_snapshot=environment_snapshot,
        )
        model_card = write_model_card(
            request=request,
            metadata=metadata,
            training_metrics=run.metrics,
            evaluation_metrics=evaluation.metrics,
            artifact_uri=run.artifact_uri,
            artifact_digest=run.artifact_digest,
            environment_snapshot=environment_snapshot,
            artifact_store=artifact_store,
        )
        signed_artifact = build_signed_registered_model_artifact(
            request=request,
            run=run,
            artifact_store=artifact_store,
            private_key_pem=_require_model_signing_private_key(settings),
            signing_key_id=settings.model_signing_key_id,
        )

        return await repository.register_model_candidate(
            request=request,
            run=run,
            evaluation=evaluation,
            metadata=metadata,
            signed_artifact_uri=signed_artifact.artifact_uri,
            signed_artifact_digest=signed_artifact.artifact_digest,
            signature_algorithm=signed_artifact.signature_algorithm,
            signature_key_id=signed_artifact.signature_key_id,
            artifact_signature=signed_artifact.artifact_signature,
            model_card_uri=model_card.model_card_uri,
            model_card_checksum=model_card.model_card_checksum,
            environment_snapshot=environment_snapshot,
        )
    finally:
        await repository.close()


@router.post("/mlops/promote")
async def promote_model(
    request: PromoteModelRequest,
    x_idempotency_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> PromoteModelResponse:
    _ = require_idempotency_key(
        x_idempotency_key,
        operation="mlops_promote",
    )
    settings = load_settings("mlops")
    await authorize_request(
        settings=settings,
        authorization=authorization,
        operation="mlops_promote",
    )
    repository = MLOpsRepository(settings)
    await repository.connect()
    try:
        response = await repository.promote_model(
            request=request,
            trace_id=get_trace_id(),
        )
        return response
    finally:
        await repository.close()


@router.get("/mlops/runs/{run_id}")
async def run_details(
    run_id: str,
    authorization: str | None = Header(default=None),
) -> MLOpsRunResponse:
    settings = load_settings("mlops")
    await authorize_request(
        settings=settings,
        authorization=authorization,
        operation="mlops_run_details",
    )
    repository = MLOpsRepository(settings)
    await repository.connect()
    try:
        return await repository.load_training_run(run_id)
    finally:
        await repository.close()


def _require_model_signing_private_key(settings: ServiceSettings) -> str:
    private_key_pem = settings.model_signing_private_key_pem
    if private_key_pem is None:
        raise ServiceError(
            error_code="ML_MODEL_SIGNING_PRIVATE_KEY_MISSING",
            message="Model signing private key is required for registry artifact signing",
            operation="mlops_register",
            status_code=500,
            hint="Configure MODEL_SIGNING_PRIVATE_KEY_PEM for mlops-service",
        )
    return private_key_pem


def _build_mlops_artifact_store(settings: ServiceSettings) -> ArtifactStore:
    try:
        return build_artifact_store(settings)
    except (ArtifactStoreError, ValueError) as exc:
        raise ServiceError(
            error_code="ML_ARTIFACT_STORAGE_CONFIGURATION_INVALID",
            message="mlops artifact storage configuration is invalid",
            operation="mlops_artifact_store",
            status_code=500,
            cause=str(exc),
            hint="Configure filesystem or Azure Blob artifact storage before registering models",
        ) from exc
