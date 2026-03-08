"""Deterministic MLOps lifecycle helpers for train/evaluate/register/promote."""

from __future__ import annotations

import hashlib
import json
import platform
from dataclasses import dataclass
from importlib import metadata
from itertools import pairwise
from typing import Any, NoReturn, cast
from uuid import uuid4

from contracts import (
    MLOpsRunResponse,
    ModelMetadata,
    RegisterModelRequest,
    TrainingMetrics,
    TrainRunRequest,
    TrainRunResponse,
)
from security import ModelSignature, sign_model_package
from shared_kernel import ArtifactStore, ArtifactStoreError, ServiceError, build_model_metadata

_ARTIFACTS_DIR = "artifacts"
_REGISTERED_ARTIFACTS_DIR = "registered_artifacts"
_MODEL_CARDS_DIR = "model_cards"
_SIGNED_PACKAGE_SCHEMA_VERSION = "credit-model-package.v2"
_FEATURE_ORDER = (
    "requested_amount",
    "debt_to_income",
    "amount_to_income",
    "credit_history_months",
    "existing_defaults",
)


@dataclass(frozen=True, slots=True)
class PolicyEvaluation:
    """Result of promotion policy checks over evaluation metrics."""

    passed: bool
    failures: list[str]


@dataclass(frozen=True, slots=True)
class ModelCardArtifact:
    """Generated model card path and checksum."""

    model_card_uri: str
    model_card_checksum: str


@dataclass(frozen=True, slots=True)
class SignedModelArtifact:
    """Signed immutable registry artifact produced at registration time."""

    artifact_uri: str
    artifact_digest: str
    signature_algorithm: str
    signature_key_id: str
    artifact_signature: str


def compute_dataset_hash(dataset_reference: str) -> str:
    """Create deterministic dataset hash from canonical dataset reference."""
    canonical_reference = dataset_reference.strip()
    return hashlib.sha256(canonical_reference.encode("utf-8")).hexdigest()


def deterministic_training_metrics(*, dataset_hash: str, random_seed: int) -> TrainingMetrics:
    """Generate deterministic reference metrics from hash + seed."""
    seed = f"{dataset_hash}:{random_seed}"
    return TrainingMetrics(
        auc=round(0.78 + (_deterministic_ratio(seed, "auc", 0) * 0.16), 4),
        precision_at_50=round(0.63 + (_deterministic_ratio(seed, "precision", 0) * 0.25), 4),
        recall_at_50=round(0.58 + (_deterministic_ratio(seed, "recall", 0) * 0.28), 4),
        calibration_error=round(
            0.01 + (_deterministic_ratio(seed, "calibration_error", 0) * 0.05),
            4,
        ),
    )


def train_deterministic_model(
    request: TrainRunRequest,
    *,
    artifact_store: ArtifactStore,
) -> TrainRunResponse:
    """Train a deterministic logistic-regression reference model and persist a JSON package."""
    np, logistic_regression_cls, standard_scaler_cls, roc_auc_score = _load_ml_dependencies()

    run_id = f"run-{uuid4().hex[:20]}"
    dataset_hash = compute_dataset_hash(request.dataset_reference)
    features, labels = _build_reference_dataset(
        np=np,
        dataset_hash=dataset_hash,
        random_seed=request.random_seed,
    )
    permutation = np.arange(labels.shape[0])
    rng = np.random.default_rng(request.random_seed)
    rng.shuffle(permutation)
    split_index = int(labels.shape[0] * 0.8)
    train_indices = permutation[:split_index]
    evaluation_indices = permutation[split_index:]

    train_features = features[train_indices]
    train_labels = labels[train_indices]
    evaluation_features = features[evaluation_indices]
    evaluation_labels = labels[evaluation_indices]

    scaler = standard_scaler_cls()
    train_features_scaled = scaler.fit_transform(train_features)
    evaluation_features_scaled = scaler.transform(evaluation_features)
    classifier = logistic_regression_cls(
        random_state=request.random_seed,
        max_iter=200,
        solver="liblinear",
    )
    classifier.fit(train_features_scaled, train_labels)
    probabilities = classifier.predict_proba(evaluation_features_scaled)[:, 1]
    metrics = _compute_training_metrics(
        np=np,
        roc_auc_score=roc_auc_score,
        labels=evaluation_labels,
        probabilities=probabilities,
    )

    artifact_payload: dict[str, Any] = {
        "schema_version": "credit-model-package.v1",
        "model_name": request.model_name,
        "dataset_hash": dataset_hash,
        "random_seed": request.random_seed,
        "algorithm": request.algorithm,
        "feature_spec_ref": request.feature_spec_ref,
        "training_spec_ref": request.training_spec_ref,
        "feature_order": list(_FEATURE_ORDER),
        "preprocessing": {
            "type": "standard_scaler",
            "means": {
                feature_name: round(float(value), 10)
                for feature_name, value in zip(_FEATURE_ORDER, scaler.mean_, strict=True)
            },
            "scales": {
                feature_name: round(float(value), 10)
                for feature_name, value in zip(_FEATURE_ORDER, scaler.scale_, strict=True)
            },
        },
        "classifier": {
            "type": "logistic_regression",
            "coefficients": {
                feature_name: round(float(value), 10)
                for feature_name, value in zip(
                    _FEATURE_ORDER,
                    classifier.coef_[0],
                    strict=True,
                )
            },
            "intercept": round(float(classifier.intercept_[0]), 10),
        },
        "operating_thresholds": {
            "approve_threshold": 0.45,
            "decline_threshold": 0.70,
        },
        "metrics": metrics.model_dump(mode="json"),
        "lineage": {
            "dataset_reference": request.dataset_reference,
            "dataset_hash": dataset_hash,
            "run_spec": {
                "algorithm": request.algorithm,
                "feature_spec_ref": request.feature_spec_ref,
                "training_spec_ref": request.training_spec_ref,
            },
        },
    }
    artifact_bytes = json.dumps(
        artifact_payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    artifact_digest = hashlib.sha256(artifact_bytes).hexdigest()

    try:
        artifact_uri = artifact_store.write_bytes(
            directory=_ARTIFACTS_DIR,
            stem=f"{request.model_name}-{artifact_digest[:16]}",
            payload=artifact_bytes,
        )
    except ArtifactStoreError as exc:
        _raise_artifact_store_error(
            exc,
            operation="mlops_train_model",
            invalid_code="ML_ARTIFACT_PATH_INVALID",
            missing_code="ML_ARTIFACT_MISSING",
            collision_code="ML_ARTIFACT_DIGEST_COLLISION",
            failure_code="ML_ARTIFACT_STORAGE_FAILURE",
            invalid_message="Artifact URI escapes the configured storage boundary",
            missing_message="Training artifact could not be read back from storage",
            collision_message="Artifact path already exists with different bytes",
            failure_message="Artifact storage backend failed while writing training artifact",
        )

    return TrainRunResponse(
        run_id=run_id,
        model_name=request.model_name,
        dataset_hash=dataset_hash,
        random_seed=request.random_seed,
        algorithm=request.algorithm,
        feature_spec_ref=request.feature_spec_ref,
        training_spec_ref=request.training_spec_ref,
        artifact_uri=artifact_uri,
        artifact_digest=artifact_digest,
        metrics=metrics,
    )


def evaluate_policy(
    *,
    metrics: TrainingMetrics,
    min_auc: float,
    max_calibration_error: float,
) -> PolicyEvaluation:
    """Evaluate policy gates for promotion eligibility."""
    failures: list[str] = []
    if metrics.auc < min_auc:
        failures.append("auc_below_threshold")
    if metrics.calibration_error > max_calibration_error:
        failures.append("calibration_error_above_threshold")
    return PolicyEvaluation(passed=len(failures) == 0, failures=failures)


def build_environment_snapshot() -> dict[str, str]:
    """Capture deterministic environment metadata for model cards."""
    package_versions: dict[str, str] = {}
    for package in ("fastapi", "pydantic", "asyncpg", "aio-pika", "numpy", "scikit-learn"):
        try:
            package_versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            package_versions[package] = "not-installed"

    snapshot = {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
    }
    for package, version in package_versions.items():
        snapshot[f"pkg_{package}"] = version
    return snapshot


def build_environment_fingerprint(snapshot: dict[str, str]) -> str:
    """Derive stable hash fingerprint from environment snapshot fields."""
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_registry_metadata(
    request: RegisterModelRequest,
    *,
    dataset_hash: str,
    random_seed: int,
    environment_snapshot: dict[str, str],
) -> ModelMetadata:
    """Compose shared reproducibility metadata for model registry records."""
    return build_model_metadata(
        model_name=request.model_name,
        model_version=request.model_version,
        dataset_hash=dataset_hash,
        random_seed=random_seed,
        environment_fingerprint=build_environment_fingerprint(environment_snapshot),
    )


def write_model_card(
    *,
    request: RegisterModelRequest,
    metadata: ModelMetadata,
    training_metrics: TrainingMetrics,
    evaluation_metrics: TrainingMetrics,
    artifact_uri: str,
    artifact_digest: str,
    environment_snapshot: dict[str, str],
    artifact_store: ArtifactStore,
) -> ModelCardArtifact:
    """Write model card JSON artifact and return path plus checksum."""
    model_card_payload = {
        "model_name": request.model_name,
        "model_version": request.model_version,
        "run_id": request.run_id,
        "evaluation_id": request.evaluation_id,
        "feature_spec_ref": request.feature_spec_ref,
        "training_spec_ref": request.training_spec_ref,
        "metadata": metadata.model_dump(mode="json"),
        "training_metrics": training_metrics.model_dump(mode="json"),
        "evaluation_metrics": evaluation_metrics.model_dump(mode="json"),
        "artifact_uri": artifact_uri,
        "artifact_digest": artifact_digest,
        "environment_snapshot": environment_snapshot,
    }
    encoded_payload = json.dumps(model_card_payload, sort_keys=True, separators=(",", ":"))
    checksum = hashlib.sha256(encoded_payload.encode("utf-8")).hexdigest()

    encoded_bytes = encoded_payload.encode("utf-8")
    try:
        model_card_uri = artifact_store.write_bytes(
            directory=_MODEL_CARDS_DIR,
            stem=f"{request.model_name}--{request.model_version}",
            payload=encoded_bytes,
        )
    except ArtifactStoreError as exc:
        _raise_artifact_store_error(
            exc,
            operation="mlops_write_model_card",
            invalid_code="ML_ARTIFACT_PATH_INVALID",
            missing_code="ML_ARTIFACT_MISSING",
            collision_code="ML_ARTIFACT_DIGEST_COLLISION",
            failure_code="ML_ARTIFACT_STORAGE_FAILURE",
            invalid_message="Model card URI escapes the configured storage boundary",
            missing_message="Model card could not be read back from storage",
            collision_message="Model card artifact already exists with different bytes",
            failure_message="Artifact storage backend failed while writing the model card",
        )

    return ModelCardArtifact(
        model_card_uri=model_card_uri,
        model_card_checksum=checksum,
    )


def build_signed_registered_model_artifact(
    *,
    request: RegisterModelRequest,
    run: MLOpsRunResponse,
    artifact_store: ArtifactStore,
    private_key_pem: str,
    signing_key_id: str,
) -> SignedModelArtifact:
    """Create a signed immutable package for registry and serving workflows."""
    try:
        raw_payload = json.loads(artifact_store.read_bytes(run.artifact_uri).decode("utf-8"))
    except ArtifactStoreError as exc:
        _raise_artifact_store_error(
            exc,
            operation="mlops_sign_registered_artifact",
            invalid_code="ML_ARTIFACT_PATH_INVALID",
            missing_code="ML_ARTIFACT_MISSING",
            collision_code="ML_ARTIFACT_DIGEST_COLLISION",
            failure_code="ML_ARTIFACT_STORAGE_FAILURE",
            invalid_message="Training artifact URI escapes the configured storage boundary",
            missing_message="Training artifact was not found in artifact storage for signing",
            collision_message="Training artifact storage collision detected during signing",
            failure_message="Artifact storage backend failed while loading training artifact",
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ServiceError(
            error_code="ML_ARTIFACT_INVALID_JSON",
            message="Training artifact is not valid JSON and cannot be signed",
            operation="mlops_sign_registered_artifact",
            status_code=500,
            cause=str(exc),
        ) from exc
    if not isinstance(raw_payload, dict):
        raise ServiceError(
            error_code="ML_ARTIFACT_INVALID_JSON",
            message="Training artifact must be a JSON object before signing",
            operation="mlops_sign_registered_artifact",
            status_code=500,
            cause=type(raw_payload).__name__,
        )

    raw_payload_mapping = cast(dict[object, object], raw_payload)
    unsigned_payload: dict[str, Any] = {
        str(key): value for key, value in raw_payload_mapping.items()
    }
    unsigned_payload["schema_version"] = _SIGNED_PACKAGE_SCHEMA_VERSION
    unsigned_payload["model_version"] = request.model_version
    canonical_unsigned_payload = _canonical_json_bytes(unsigned_payload)
    try:
        signature = sign_model_package(
            payload=canonical_unsigned_payload,
            private_key_pem=private_key_pem,
            key_id=signing_key_id,
        )
    except ValueError as exc:
        raise ServiceError(
            error_code="ML_MODEL_SIGNING_CONFIGURATION_INVALID",
            message="Model signing configuration is invalid",
            operation="mlops_sign_registered_artifact",
            status_code=500,
            cause=str(exc),
        ) from exc

    signed_payload: dict[str, Any] = dict(unsigned_payload)
    signed_payload["signature"] = _signature_payload(signature)
    artifact_bytes = _canonical_json_bytes(signed_payload)
    artifact_digest = hashlib.sha256(artifact_bytes).hexdigest()

    try:
        artifact_uri = artifact_store.write_bytes(
            directory=_REGISTERED_ARTIFACTS_DIR,
            stem=f"{request.model_name}--{request.model_version}--{artifact_digest[:16]}",
            payload=artifact_bytes,
        )
    except ArtifactStoreError as exc:
        _raise_artifact_store_error(
            exc,
            operation="mlops_sign_registered_artifact",
            invalid_code="ML_ARTIFACT_PATH_INVALID",
            missing_code="ML_ARTIFACT_MISSING",
            collision_code="ML_ARTIFACT_DIGEST_COLLISION",
            failure_code="ML_ARTIFACT_STORAGE_FAILURE",
            invalid_message="Signed registry artifact URI escapes the configured storage boundary",
            missing_message="Signed registry artifact could not be read back from storage",
            collision_message="Signed registry artifact path already exists with different bytes",
            failure_message=(
                "Artifact storage backend failed while writing the signed registry artifact"
            ),
        )

    return SignedModelArtifact(
        artifact_uri=artifact_uri,
        artifact_digest=artifact_digest,
        signature_algorithm=signature.algorithm,
        signature_key_id=signature.key_id,
        artifact_signature=signature.value,
    )


def _build_reference_dataset(
    *,
    np: Any,
    dataset_hash: str,
    random_seed: int,
) -> tuple[Any, Any]:
    rng = np.random.default_rng(random_seed)
    sample_size = 384
    dataset_adjustment = (_deterministic_ratio(dataset_hash, "dataset_adjustment", 0) - 0.5) * 0.10
    monthly_income = rng.normal(6200.0, 1750.0, sample_size).clip(2200.0, 25000.0)
    monthly_debt = monthly_income * rng.uniform(0.06, 0.68, sample_size)
    requested_amount = monthly_income * 12.0 * rng.uniform(0.04, 0.42, sample_size)
    debt_to_income = monthly_debt / monthly_income
    amount_to_income = requested_amount / (monthly_income * 12.0)
    credit_history_months = rng.integers(6, 180, sample_size)
    existing_defaults = rng.choice([0, 1, 2, 3], sample_size, p=[0.79, 0.15, 0.05, 0.01])
    noise = rng.normal(0.0, 0.15, sample_size)
    logits = (
        (-2.0 + dataset_adjustment)
        + (2.4 * debt_to_income)
        + (2.1 * amount_to_income)
        - (0.013 * credit_history_months)
        + (0.95 * existing_defaults)
        + noise
    )
    probabilities = 1.0 / (1.0 + np.exp(-logits))
    labels = rng.binomial(1, probabilities)
    features = np.column_stack(
        [
            requested_amount,
            debt_to_income,
            amount_to_income,
            credit_history_months.astype(float),
            existing_defaults.astype(float),
        ]
    )
    return features, labels


def _compute_training_metrics(
    *,
    np: Any,
    roc_auc_score: Any,
    labels: Any,
    probabilities: Any,
) -> TrainingMetrics:
    auc = float(roc_auc_score(labels, probabilities))
    top_k = min(50, int(probabilities.shape[0]))
    if top_k <= 0:
        raise ServiceError(
            error_code="ML_EVALUATION_INVALID",
            message="Evaluation set is empty",
            operation="mlops_train_model",
            status_code=500,
        )
    selected = _top_k_flags(np=np, probabilities=probabilities, top_k=top_k)
    true_positive = float(np.logical_and(selected == 1, labels == 1).sum())
    actual_positive = float((labels == 1).sum())
    precision_at_50 = true_positive / float(top_k)
    recall_at_50 = 0.0 if actual_positive == 0 else true_positive / actual_positive
    calibration_error = _expected_calibration_error(
        np=np, labels=labels, probabilities=probabilities
    )
    return TrainingMetrics(
        auc=round(auc, 4),
        precision_at_50=round(precision_at_50, 4),
        recall_at_50=round(recall_at_50, 4),
        calibration_error=round(calibration_error, 4),
    )


def _top_k_flags(*, np: Any, probabilities: Any, top_k: int) -> Any:
    selected = np.zeros(probabilities.shape[0], dtype=int)
    candidate_indices = np.argsort(probabilities)[-top_k:]
    selected[candidate_indices] = 1
    return selected


def _expected_calibration_error(*, np: Any, labels: Any, probabilities: Any) -> float:
    bin_edges = np.quantile(probabilities, np.linspace(0.0, 1.0, 5))
    bin_edges[0] = 0.0
    bin_edges[-1] = 1.0
    calibration_error = 0.0
    last_index = len(bin_edges) - 2
    for index, (lower, upper) in enumerate(pairwise(bin_edges)):
        mask = (probabilities >= lower) & (probabilities < upper)
        if index == last_index:
            mask = (probabilities >= lower) & (probabilities <= upper)
        if int(mask.sum()) == 0:
            continue
        observed = float(labels[mask].mean())
        predicted = float(probabilities[mask].mean())
        weight = float(mask.sum()) / float(probabilities.shape[0])
        calibration_error += abs(observed - predicted) * weight
    return calibration_error


def _load_ml_dependencies() -> tuple[Any, Any, Any, Any]:
    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
        from sklearn.metrics import roc_auc_score
        from sklearn.preprocessing import StandardScaler
    except ModuleNotFoundError as exc:
        raise ServiceError(
            error_code="ML_DEPENDENCY_MISSING",
            message="Required ML training dependency is not installed",
            operation="mlops_train_model",
            status_code=500,
            cause=str(exc),
            hint="Install numpy and scikit-learn before running train/register flows",
        ) from exc
    return np, LogisticRegression, StandardScaler, roc_auc_score


def _canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _signature_payload(signature: ModelSignature) -> dict[str, str]:
    return {
        "schema_version": signature.schema_version,
        "algorithm": signature.algorithm,
        "key_id": signature.key_id,
        "value": signature.value,
    }


def _deterministic_ratio(seed: str, label: str, index: int) -> float:
    """Return deterministic ratio in [0, 1] from hashed seed material."""
    digest = hashlib.sha256(f"{seed}:{label}:{index}".encode()).digest()
    numerator = int.from_bytes(digest[:8], byteorder="big", signed=False)
    denominator = float((1 << 64) - 1)
    return numerator / denominator


def _raise_artifact_store_error(
    exc: ArtifactStoreError,
    *,
    operation: str,
    invalid_code: str,
    missing_code: str,
    collision_code: str,
    failure_code: str,
    invalid_message: str,
    missing_message: str,
    collision_message: str,
    failure_message: str,
) -> NoReturn:
    if exc.kind == "invalid_uri":
        raise ServiceError(
            error_code=invalid_code,
            message=invalid_message,
            operation=operation,
            status_code=400,
            cause=exc.detail,
        ) from exc
    if exc.kind == "not_found":
        raise ServiceError(
            error_code=missing_code,
            message=missing_message,
            operation=operation,
            status_code=500,
            cause=exc.detail,
        ) from exc
    if exc.kind == "collision":
        raise ServiceError(
            error_code=collision_code,
            message=collision_message,
            operation=operation,
            status_code=500,
            cause=exc.detail,
        ) from exc
    raise ServiceError(
        error_code=failure_code,
        message=failure_message,
        operation=operation,
        status_code=500,
        cause=exc.detail,
    ) from exc
