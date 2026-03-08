"""Generate deterministic MLOps evidence artifacts for recruiter demos."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from mlops_service.lifecycle import (
    build_environment_snapshot,
    build_registry_metadata,
    evaluate_policy,
    train_deterministic_model,
    write_model_card,
)

from contracts import RegisterModelRequest, TrainRunRequest
from shared_kernel import build_filesystem_artifact_store

DEFAULT_MODEL_NAME = "credit_default_risk"
DEFAULT_MODEL_VERSION = "v1.0.0"
DEFAULT_DATASET_REFERENCE = "synthetic://credit/v1?split=train"
DEFAULT_RANDOM_SEED = 0
DEFAULT_MIN_AUC = 0.78
DEFAULT_MAX_CALIBRATION_ERROR = 0.06


def generate_evidence(*, output_path: Path, artifact_root: Path) -> dict[str, Any]:
    """Generate deterministic train/register evidence and persist report JSON."""
    artifact_root.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_store = build_filesystem_artifact_store(artifact_root)

    request = TrainRunRequest(
        model_name=DEFAULT_MODEL_NAME,
        dataset_reference=DEFAULT_DATASET_REFERENCE,
        random_seed=DEFAULT_RANDOM_SEED,
        algorithm="sklearn_logistic_regression",
        feature_spec_ref="credit-feature-spec/v1",
        training_spec_ref="credit-training-spec/v1",
    )
    first_run = train_deterministic_model(request, artifact_store=artifact_store)
    second_run = train_deterministic_model(request, artifact_store=artifact_store)
    digest_stable = first_run.artifact_digest == second_run.artifact_digest
    if not digest_stable:
        raise RuntimeError(
            "deterministic training digest mismatch for identical dataset reference and seed"
        )

    policy = evaluate_policy(
        metrics=first_run.metrics,
        min_auc=DEFAULT_MIN_AUC,
        max_calibration_error=DEFAULT_MAX_CALIBRATION_ERROR,
    )
    if not policy.passed:
        raise RuntimeError(
            f"policy gate failed for deterministic run: {', '.join(policy.failures)}"
        )

    register_request = RegisterModelRequest(
        model_name=DEFAULT_MODEL_NAME,
        model_version=DEFAULT_MODEL_VERSION,
        run_id=first_run.run_id,
        evaluation_id=f"eval-{uuid4().hex[:20]}",
        feature_spec_ref=first_run.feature_spec_ref,
        training_spec_ref=first_run.training_spec_ref,
    )
    environment_snapshot = build_environment_snapshot()
    metadata = build_registry_metadata(
        register_request,
        dataset_hash=first_run.dataset_hash,
        random_seed=first_run.random_seed,
        environment_snapshot=environment_snapshot,
    )
    model_card = write_model_card(
        request=register_request,
        metadata=metadata,
        training_metrics=first_run.metrics,
        evaluation_metrics=first_run.metrics,
        artifact_uri=first_run.artifact_uri,
        artifact_digest=first_run.artifact_digest,
        environment_snapshot=environment_snapshot,
        artifact_store=artifact_store,
    )

    payload: dict[str, Any] = {
        "model_name": DEFAULT_MODEL_NAME,
        "model_version": DEFAULT_MODEL_VERSION,
        "dataset_reference": DEFAULT_DATASET_REFERENCE,
        "random_seed": DEFAULT_RANDOM_SEED,
        "policy_thresholds": {
            "min_auc": DEFAULT_MIN_AUC,
            "max_calibration_error": DEFAULT_MAX_CALIBRATION_ERROR,
        },
        "training_run": first_run.model_dump(mode="json"),
        "determinism_check": {
            "second_run_id": second_run.run_id,
            "digest_stable_across_runs": digest_stable,
        },
        "policy_result": {
            "passed": policy.passed,
            "failures": policy.failures,
        },
        "metadata": metadata.model_dump(mode="json"),
        "model_card": {
            "uri": model_card.model_card_uri,
            "checksum": model_card.model_card_checksum,
        },
    }
    output_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate deterministic MLOps evidence artifacts for recruiter demos",
    )
    parser.add_argument(
        "--output",
        default="build/recruiter-ml-evidence.json",
        help="Output JSON evidence file path",
    )
    parser.add_argument(
        "--artifact-root",
        default="build/recruiter-mlops",
        help="Artifact root directory for model artifacts and model cards",
    )
    args = parser.parse_args()

    output_path = Path(args.output).resolve()
    artifact_root = Path(args.artifact_root).resolve()
    payload = generate_evidence(output_path=output_path, artifact_root=artifact_root)

    print(
        "[mlops-evidence] generated "
        f"artifact_digest={payload['training_run']['artifact_digest']} "
        f"model_card={payload['model_card']['uri']}"
    )
    print(f"[mlops-evidence] report={output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
