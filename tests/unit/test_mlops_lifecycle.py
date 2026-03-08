from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from mlops_service.lifecycle import (
    build_environment_fingerprint,
    build_environment_snapshot,
    build_registry_metadata,
    build_signed_registered_model_artifact,
    compute_dataset_hash,
    deterministic_training_metrics,
    evaluate_policy,
    train_deterministic_model,
    write_model_card,
)

from contracts import MLOpsRunResponse, RegisterModelRequest, TrainingMetrics, TrainRunRequest
from shared_kernel import build_filesystem_artifact_store
from tests.defaults import TEST_MODEL_SIGNING_KEY_ID, TEST_MODEL_SIGNING_PRIVATE_KEY_PEM


@pytest.mark.unit
def test_dataset_hash_is_deterministic() -> None:
    first = compute_dataset_hash("s3://bucket/path/dataset-v1.parquet")
    second = compute_dataset_hash("s3://bucket/path/dataset-v1.parquet")
    assert first == second
    assert len(first) == 64


@pytest.mark.unit
def test_deterministic_training_metrics_same_seed_same_output() -> None:
    dataset_hash = compute_dataset_hash("dataset://credit/v1")
    first = deterministic_training_metrics(dataset_hash=dataset_hash, random_seed=42)
    second = deterministic_training_metrics(dataset_hash=dataset_hash, random_seed=42)
    assert first == second


@pytest.mark.unit
def test_train_deterministic_model_writes_stable_artifact_payload(tmp_path: Path) -> None:
    artifact_store = build_filesystem_artifact_store(tmp_path)
    request = TrainRunRequest(
        model_name="credit-risk",
        dataset_reference="dataset://credit/v1",
        random_seed=7,
    )

    run = train_deterministic_model(request, artifact_store=artifact_store)
    artifact_path = Path(run.artifact_uri)

    assert artifact_path.exists()
    artifact_bytes = artifact_path.read_bytes()
    payload = json.loads(artifact_bytes.decode("utf-8"))
    assert payload["model_name"] == "credit-risk"
    assert payload["lineage"]["dataset_hash"] == run.dataset_hash
    assert payload["algorithm"] == "sklearn_logistic_regression"
    assert payload["feature_order"] == [
        "requested_amount",
        "debt_to_income",
        "amount_to_income",
        "credit_history_months",
        "existing_defaults",
    ]
    assert payload["metrics"]["auc"] == run.metrics.auc
    assert hashlib.sha256(artifact_bytes).hexdigest() == run.artifact_digest


@pytest.mark.unit
def test_policy_evaluation_reports_failures() -> None:
    metrics = TrainingMetrics(
        auc=0.65,
        precision_at_50=0.77,
        recall_at_50=0.76,
        calibration_error=0.09,
    )
    policy = evaluate_policy(
        metrics=metrics,
        min_auc=0.78,
        max_calibration_error=0.06,
    )
    assert policy.passed is False
    assert "auc_below_threshold" in policy.failures
    assert "calibration_error_above_threshold" in policy.failures


@pytest.mark.unit
def test_model_card_contains_environment_and_reproducibility_fields(tmp_path: Path) -> None:
    artifact_store = build_filesystem_artifact_store(tmp_path)
    request = RegisterModelRequest(
        model_name="credit-risk",
        model_version="v1.0.0",
        run_id="run-12345678",
        evaluation_id="eval-12345678",
    )
    snapshot = build_environment_snapshot()
    metadata = build_registry_metadata(
        request,
        dataset_hash=compute_dataset_hash("dataset://credit/v1"),
        random_seed=11,
        environment_snapshot=snapshot,
    )
    card = write_model_card(
        request=request,
        metadata=metadata,
        training_metrics=TrainingMetrics(
            auc=0.82,
            precision_at_50=0.77,
            recall_at_50=0.73,
            calibration_error=0.03,
        ),
        evaluation_metrics=TrainingMetrics(
            auc=0.81,
            precision_at_50=0.75,
            recall_at_50=0.72,
            calibration_error=0.04,
        ),
        artifact_uri=str(tmp_path / "artifact.json"),
        artifact_digest="a" * 64,
        environment_snapshot=snapshot,
        artifact_store=artifact_store,
    )

    model_card_path = Path(card.model_card_uri)
    assert model_card_path.exists()
    loaded = json.loads(model_card_path.read_text(encoding="utf-8"))
    assert loaded["metadata"]["environment_fingerprint"] == metadata.environment_fingerprint
    assert loaded["environment_snapshot"] == snapshot
    assert loaded["artifact_digest"] == "a" * 64
    assert build_environment_fingerprint(snapshot) == metadata.environment_fingerprint


@pytest.mark.unit
def test_training_artifact_digest_is_deterministic_across_runs(tmp_path: Path) -> None:
    artifact_store = build_filesystem_artifact_store(tmp_path)
    request = TrainRunRequest(
        model_name="credit-risk",
        dataset_reference="dataset://credit/v1",
        random_seed=99,
    )

    first = train_deterministic_model(request, artifact_store=artifact_store)
    second = train_deterministic_model(request, artifact_store=artifact_store)

    assert first.artifact_digest == second.artifact_digest
    assert first.run_id != second.run_id


@pytest.mark.unit
def test_build_signed_registered_model_artifact_writes_verifiable_package(tmp_path: Path) -> None:
    artifact_store = build_filesystem_artifact_store(tmp_path)
    train_request = TrainRunRequest(
        model_name="credit-risk",
        dataset_reference="dataset://credit/v1",
        random_seed=13,
    )
    run = train_deterministic_model(train_request, artifact_store=artifact_store)
    register_request = RegisterModelRequest(
        model_name="credit-risk",
        model_version="v1.0.1",
        run_id=run.run_id,
        evaluation_id="eval-12345678",
        feature_spec_ref=run.feature_spec_ref,
        training_spec_ref=run.training_spec_ref,
    )
    signed_artifact = build_signed_registered_model_artifact(
        request=register_request,
        run=MLOpsRunResponse.model_validate(
            {
                "run_id": run.run_id,
                "model_name": run.model_name,
                "dataset_hash": run.dataset_hash,
                "random_seed": run.random_seed,
                "algorithm": run.algorithm,
                "feature_spec_ref": run.feature_spec_ref,
                "training_spec_ref": run.training_spec_ref,
                "artifact_uri": run.artifact_uri,
                "artifact_digest": run.artifact_digest,
                "status": "succeeded",
                "metrics": run.metrics.model_dump(mode="json"),
                "created_at": "2026-01-01T00:00:00+00:00",
                "completed_at": "2026-01-01T00:00:00+00:00",
            }
        ),
        artifact_store=artifact_store,
        private_key_pem=TEST_MODEL_SIGNING_PRIVATE_KEY_PEM,
        signing_key_id=TEST_MODEL_SIGNING_KEY_ID,
    )

    artifact_path = Path(signed_artifact.artifact_uri)
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "credit-model-package.v2"
    assert payload["model_version"] == "v1.0.1"
    assert payload["signature"]["algorithm"] == "ed25519"
    assert payload["signature"]["key_id"] == TEST_MODEL_SIGNING_KEY_ID
    assert signed_artifact.signature_key_id == TEST_MODEL_SIGNING_KEY_ID
