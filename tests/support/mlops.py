from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from mlops_service.lifecycle import (
    build_environment_snapshot,
    build_registry_metadata,
    build_signed_registered_model_artifact,
    train_deterministic_model,
    write_model_card,
)
from mlops_service.repositories import MLOpsRepository

from contracts import PromoteModelRequest, RegisterModelRequest, TrainRunRequest
from shared_kernel import ServiceSettings, build_filesystem_artifact_store, load_settings
from tests.defaults import (
    TEST_MODEL_SIGNING_KEY_ID,
    TEST_MODEL_SIGNING_PRIVATE_KEY_PEM,
)


async def seed_promoted_scoring_model(
    *,
    settings_scoring: ServiceSettings,
    trace_id: str,
) -> None:
    settings_mlops = load_settings("mlops")
    artifact_store = build_filesystem_artifact_store(Path(settings_mlops.artifact_root_dir))
    promoted_model_version = f"v1.0.0-{uuid4().hex[:8]}"
    repository = MLOpsRepository(settings_mlops)
    await repository.connect()
    try:
        train_request = TrainRunRequest(
            model_name=settings_scoring.scoring_model_name,
            dataset_reference="synthetic://integration/credit/v1",
            random_seed=11,
        )
        run = train_deterministic_model(
            train_request,
            artifact_store=artifact_store,
        )
        await repository.persist_training_run(
            run=run,
            dataset_reference=train_request.dataset_reference,
        )
        evaluation = await repository.persist_evaluation_run(
            run_id=run.run_id,
            metrics=run.metrics,
            passed_policy=True,
            policy_failures=[],
        )
        register_request = RegisterModelRequest(
            model_name=settings_scoring.scoring_model_name,
            model_version=promoted_model_version,
            run_id=run.run_id,
            evaluation_id=evaluation.evaluation_id,
            feature_spec_ref=run.feature_spec_ref,
            training_spec_ref=run.training_spec_ref,
        )
        environment_snapshot = build_environment_snapshot()
        metadata = build_registry_metadata(
            register_request,
            dataset_hash=run.dataset_hash,
            random_seed=run.random_seed,
            environment_snapshot=environment_snapshot,
        )
        model_card = write_model_card(
            request=register_request,
            metadata=metadata,
            training_metrics=run.metrics,
            evaluation_metrics=evaluation.metrics,
            artifact_uri=run.artifact_uri,
            artifact_digest=run.artifact_digest,
            environment_snapshot=environment_snapshot,
            artifact_store=artifact_store,
        )
        signed_artifact = build_signed_registered_model_artifact(
            request=register_request,
            run=await repository.load_training_run(run.run_id),
            artifact_store=artifact_store,
            private_key_pem=TEST_MODEL_SIGNING_PRIVATE_KEY_PEM,
            signing_key_id=TEST_MODEL_SIGNING_KEY_ID,
        )
        await repository.register_model_candidate(
            request=register_request,
            run=await repository.load_training_run(run.run_id),
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
        await repository.promote_model(
            request=PromoteModelRequest(
                model_name=settings_scoring.scoring_model_name,
                model_version=promoted_model_version,
                stage=settings_scoring.scoring_model_stage,
                approved_by="integration-test",
                approval_ticket=f"ticket-{uuid4().hex[:16]}",
                risk_signoff_ref="integration-risk-signoff",
            ),
            trace_id=trace_id,
        )
    finally:
        await repository.close()
