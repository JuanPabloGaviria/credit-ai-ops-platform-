"""Promoted scoring model resolution and deterministic inference runtime."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Literal, NoReturn

from pydantic import BaseModel, ConfigDict, Field

from contracts import FeatureVector, ScorePrediction, TrainingMetrics
from security import ModelSignature, verify_model_package_signature
from shared_kernel import (
    ArtifactStoreError,
    DatabaseExecutor,
    ServiceError,
    ServiceSettings,
    build_artifact_store,
)

_EXPECTED_SCHEMA_VERSION = "credit-model-package.v2"
_FEATURE_ORDER = (
    "requested_amount",
    "debt_to_income",
    "amount_to_income",
    "credit_history_months",
    "existing_defaults",
)
_REASON_CODES_BY_FEATURE: dict[str, str] = {
    "requested_amount": "ELEVATED_REQUEST_AMOUNT",
    "debt_to_income": "HIGH_DTI",
    "amount_to_income": "HIGH_REQUEST_RATIO",
    "credit_history_months": "SHORT_HISTORY",
    "existing_defaults": "PRIOR_DEFAULT",
}
_MODEL_CACHE: dict[tuple[str, str, str], ActiveScoringModel] = {}


class _PreprocessingArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["standard_scaler"]
    means: dict[str, float]
    scales: dict[str, float]


class _ClassifierArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["logistic_regression"]
    coefficients: dict[str, float]
    intercept: float


class _OperatingThresholdsArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approve_threshold: float = Field(ge=0, le=1)
    decline_threshold: float = Field(ge=0, le=1)


class _RuntimeModelPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["credit-model-package.v2"]
    model_name: str = Field(min_length=3)
    model_version: str = Field(min_length=3)
    dataset_hash: str = Field(min_length=16)
    random_seed: int = Field(ge=0)
    algorithm: Literal["sklearn_logistic_regression"]
    feature_spec_ref: str = Field(min_length=3)
    training_spec_ref: str = Field(min_length=3)
    feature_order: list[str]
    preprocessing: _PreprocessingArtifact
    classifier: _ClassifierArtifact
    operating_thresholds: _OperatingThresholdsArtifact
    metrics: TrainingMetrics
    lineage: dict[str, object]
    signature: dict[str, str]


@dataclass(frozen=True, slots=True)
class ActiveScoringModel:
    """Validated promoted scoring model package with inference helper."""

    model_name: str
    model_version: str
    feature_spec_ref: str
    training_spec_ref: str
    artifact_uri: str
    artifact_digest: str
    signature_algorithm: str
    signature_key_id: str
    artifact_signature: str
    package: _RuntimeModelPackage

    def score(self, features: FeatureVector) -> ScorePrediction:
        raw_values = {
            "requested_amount": float(features.requested_amount),
            "debt_to_income": float(features.debt_to_income),
            "amount_to_income": float(features.amount_to_income),
            "credit_history_months": float(features.credit_history_months),
            "existing_defaults": float(features.existing_defaults),
        }
        logit = float(self.package.classifier.intercept)
        contributions: list[tuple[str, float]] = []
        for feature_name in self.package.feature_order:
            mean = self.package.preprocessing.means.get(feature_name)
            scale = self.package.preprocessing.scales.get(feature_name)
            coefficient = self.package.classifier.coefficients.get(feature_name)
            raw_value = raw_values.get(feature_name)
            if mean is None or scale is None or coefficient is None or raw_value is None:
                raise ServiceError(
                    error_code="SCORING_MODEL_ARTIFACT_INVALID",
                    message="Promoted scoring model package is missing required feature metadata",
                    operation="scoring_model_score",
                    status_code=500,
                    cause=feature_name,
                )
            if scale <= 0:
                raise ServiceError(
                    error_code="SCORING_MODEL_ARTIFACT_INVALID",
                    message="Promoted scoring model scale must be greater than zero",
                    operation="scoring_model_score",
                    status_code=500,
                    cause=feature_name,
                )
            standardized_value = (raw_value - mean) / scale
            contribution = float(coefficient) * standardized_value
            logit += contribution
            contributions.append((feature_name, contribution))

        stabilized_logit = max(min(logit, 60.0), -60.0)
        probability = 1.0 / (1.0 + math.exp(-stabilized_logit))
        reason_codes = _build_reason_codes(contributions)
        return ScorePrediction(
            application_id=features.application_id,
            requested_amount=features.requested_amount,
            risk_score=round(probability, 6),
            model_version=self.model_version,
            reason_codes=reason_codes,
        )


async def resolve_active_scoring_model(
    *,
    db: DatabaseExecutor,
    settings: ServiceSettings,
) -> ActiveScoringModel:
    """Resolve the latest promoted model configured for the scoring service."""
    row = await db.fetchrow(
        """
        SELECT
            mr.model_name,
            mr.model_version,
            mr.feature_spec_ref,
            mr.training_spec_ref,
            mr.algorithm,
            mr.artifact_uri,
            mr.artifact_digest,
            mr.signature_algorithm,
            mr.signature_key_id,
            mr.artifact_signature
        FROM model_stage_assignments msa
        JOIN model_registry mr
          ON mr.model_name = msa.model_name
         AND mr.model_version = msa.model_version
        WHERE msa.model_name = $1
          AND msa.stage = $2
        ORDER BY msa.promoted_at DESC
        LIMIT 1
        """,
        settings.scoring_model_name,
        settings.scoring_model_stage,
    )
    if row is None:
        raise ServiceError(
            error_code="SCORING_MODEL_NOT_PROMOTED",
            message="No promoted scoring model is available for the configured stage",
            operation="scoring_model_resolve",
            status_code=503,
            cause=f"{settings.scoring_model_name}:{settings.scoring_model_stage}",
            hint="Promote a registered model before processing scoring requests",
        )

    artifact_uri = _require_non_empty_str(row, "artifact_uri")
    artifact_digest = _require_non_empty_str(row, "artifact_digest")
    model_version = _require_non_empty_str(row, "model_version")
    cache_key = (artifact_uri, artifact_digest, model_version)
    cached_model = _MODEL_CACHE.get(cache_key)
    if cached_model is not None:
        return cached_model

    try:
        artifact_store = build_artifact_store(settings)
    except ValueError as exc:
        raise ServiceError(
            error_code="SCORING_ARTIFACT_STORAGE_CONFIGURATION_INVALID",
            message="Scoring service artifact storage configuration is invalid",
            operation="scoring_model_resolve",
            status_code=500,
            cause=str(exc),
        ) from exc
    try:
        artifact_bytes = await asyncio.to_thread(artifact_store.read_bytes, artifact_uri)
    except ArtifactStoreError as exc:
        _raise_artifact_store_error(exc)
    observed_digest = hashlib.sha256(artifact_bytes).hexdigest()
    if observed_digest != artifact_digest:
        raise ServiceError(
            error_code="SCORING_MODEL_ARTIFACT_DIGEST_MISMATCH",
            message="Promoted scoring model artifact digest does not match registry metadata",
            operation="scoring_model_resolve",
            status_code=500,
            cause=f"{artifact_uri}:{observed_digest}",
        )

    try:
        raw_payload = json.loads(artifact_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ServiceError(
            error_code="SCORING_MODEL_ARTIFACT_INVALID_JSON",
            message="Promoted scoring model artifact is not valid JSON",
            operation="scoring_model_resolve",
            status_code=500,
            cause=str(exc),
        ) from exc
    package = _RuntimeModelPackage.model_validate(raw_payload)
    _validate_package(
        package=package,
        settings=settings,
        expected_model_name=_require_non_empty_str(row, "model_name"),
        expected_model_version=model_version,
        expected_feature_spec_ref=_require_non_empty_str(row, "feature_spec_ref"),
        expected_training_spec_ref=_require_non_empty_str(row, "training_spec_ref"),
        expected_algorithm=_require_non_empty_str(row, "algorithm"),
        expected_signature_algorithm=_require_non_empty_str(row, "signature_algorithm"),
        expected_signature_key_id=_require_non_empty_str(row, "signature_key_id"),
        expected_artifact_signature=_require_non_empty_str(row, "artifact_signature"),
    )
    active_model = ActiveScoringModel(
        model_name=_require_non_empty_str(row, "model_name"),
        model_version=model_version,
        feature_spec_ref=_require_non_empty_str(row, "feature_spec_ref"),
        training_spec_ref=_require_non_empty_str(row, "training_spec_ref"),
        artifact_uri=artifact_uri,
        artifact_digest=artifact_digest,
        signature_algorithm=_require_non_empty_str(row, "signature_algorithm"),
        signature_key_id=_require_non_empty_str(row, "signature_key_id"),
        artifact_signature=_require_non_empty_str(row, "artifact_signature"),
        package=package,
    )
    _MODEL_CACHE[cache_key] = active_model
    return active_model


def _validate_package(
    *,
    package: _RuntimeModelPackage,
    settings: ServiceSettings,
    expected_model_name: str,
    expected_model_version: str,
    expected_feature_spec_ref: str,
    expected_training_spec_ref: str,
    expected_algorithm: str,
    expected_signature_algorithm: str,
    expected_signature_key_id: str,
    expected_artifact_signature: str,
) -> None:
    if package.schema_version != _EXPECTED_SCHEMA_VERSION:
        raise ServiceError(
            error_code="SCORING_MODEL_ARTIFACT_INVALID",
            message="Promoted scoring model artifact uses an unsupported schema version",
            operation="scoring_model_resolve",
            status_code=500,
            cause=package.schema_version,
        )
    if package.model_name != expected_model_name:
        raise ServiceError(
            error_code="SCORING_MODEL_ARTIFACT_INVALID",
            message="Promoted scoring model artifact model name does not match registry metadata",
            operation="scoring_model_resolve",
            status_code=500,
            cause=f"{package.model_name}:{expected_model_name}",
        )
    if package.model_version != expected_model_version:
        raise ServiceError(
            error_code="SCORING_MODEL_ARTIFACT_INVALID",
            message=(
                "Promoted scoring model artifact model version does not match "
                "registry metadata"
            ),
            operation="scoring_model_resolve",
            status_code=500,
            cause=f"{package.model_version}:{expected_model_version}",
        )
    if package.algorithm != expected_algorithm:
        raise ServiceError(
            error_code="SCORING_MODEL_ARTIFACT_INVALID",
            message="Promoted scoring model algorithm does not match registry metadata",
            operation="scoring_model_resolve",
            status_code=500,
            cause=f"{package.algorithm}:{expected_algorithm}",
        )
    if package.feature_spec_ref != expected_feature_spec_ref:
        raise ServiceError(
            error_code="SCORING_MODEL_ARTIFACT_INVALID",
            message="Promoted scoring model feature spec does not match registry metadata",
            operation="scoring_model_resolve",
            status_code=500,
            cause=f"{package.feature_spec_ref}:{expected_feature_spec_ref}",
        )
    if package.training_spec_ref != expected_training_spec_ref:
        raise ServiceError(
            error_code="SCORING_MODEL_ARTIFACT_INVALID",
            message="Promoted scoring model training spec does not match registry metadata",
            operation="scoring_model_resolve",
            status_code=500,
            cause=f"{package.training_spec_ref}:{expected_training_spec_ref}",
        )
    if tuple(package.feature_order) != _FEATURE_ORDER:
        raise ServiceError(
            error_code="SCORING_MODEL_ARTIFACT_INVALID",
            message="Promoted scoring model feature order does not match the runtime contract",
            operation="scoring_model_resolve",
            status_code=500,
            cause=",".join(package.feature_order),
        )
    if expected_model_version.strip() == "":
        raise ServiceError(
            error_code="SCORING_MODEL_ARTIFACT_INVALID",
            message="Promoted scoring model version is missing from registry metadata",
            operation="scoring_model_resolve",
            status_code=500,
        )
    signature = _load_signature(package.signature)
    if signature.algorithm != expected_signature_algorithm:
        raise ServiceError(
            error_code="SCORING_MODEL_ARTIFACT_INVALID",
            message="Promoted scoring model signature algorithm does not match registry metadata",
            operation="scoring_model_resolve",
            status_code=500,
            cause=f"{signature.algorithm}:{expected_signature_algorithm}",
        )
    if signature.key_id != expected_signature_key_id:
        raise ServiceError(
            error_code="SCORING_MODEL_ARTIFACT_INVALID",
            message="Promoted scoring model signature key ID does not match registry metadata",
            operation="scoring_model_resolve",
            status_code=500,
            cause=f"{signature.key_id}:{expected_signature_key_id}",
        )
    if signature.value != expected_artifact_signature:
        raise ServiceError(
            error_code="SCORING_MODEL_ARTIFACT_INVALID",
            message="Promoted scoring model signature value does not match registry metadata",
            operation="scoring_model_resolve",
            status_code=500,
            cause=expected_model_version,
        )
    try:
        verify_model_package_signature(
            payload=_canonical_payload_bytes(package),
            signature=signature,
            public_key_pem=_require_model_signing_public_key(settings),
        )
    except ValueError as exc:
        raise ServiceError(
            error_code="SCORING_MODEL_SIGNATURE_INVALID",
            message="Promoted scoring model artifact signature verification failed",
            operation="scoring_model_resolve",
            status_code=500,
            cause=str(exc),
        ) from exc


def _build_reason_codes(contributions: list[tuple[str, float]]) -> list[str]:
    positive_contributions = sorted(
        (
            (_REASON_CODES_BY_FEATURE[feature_name], contribution)
            for feature_name, contribution in contributions
            if contribution > 0 and feature_name in _REASON_CODES_BY_FEATURE
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    ordered_codes: list[str] = []
    for code, _contribution in positive_contributions:
        if code not in ordered_codes:
            ordered_codes.append(code)
        if len(ordered_codes) == 3:
            break
    if ordered_codes:
        return ordered_codes
    return ["LOW_RISK_PROFILE"]


def _load_signature(raw_signature: dict[str, str]) -> ModelSignature:
    return ModelSignature(
        schema_version=_require_mapping_str(raw_signature, "schema_version"),
        algorithm=_require_mapping_str(raw_signature, "algorithm"),
        key_id=_require_mapping_str(raw_signature, "key_id"),
        value=_require_mapping_str(raw_signature, "value"),
    )


def _canonical_payload_bytes(package: _RuntimeModelPackage) -> bytes:
    payload = package.model_dump(mode="json")
    payload.pop("signature", None)
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _require_mapping_str(payload: dict[str, str], key: str) -> str:
    value = payload.get(key)
    if isinstance(value, str) and value.strip() != "":
        return value
    raise ServiceError(
        error_code="SCORING_MODEL_ARTIFACT_INVALID",
        message="Promoted scoring model signature metadata is missing a required string field",
        operation="scoring_model_resolve",
        status_code=500,
        cause=key,
    )


def _require_model_signing_public_key(settings: ServiceSettings) -> str:
    public_key_pem = settings.model_signing_public_key_pem
    if public_key_pem is None:
        raise ServiceError(
            error_code="SCORING_MODEL_SIGNATURE_PUBLIC_KEY_MISSING",
            message=(
                "Scoring service model signing public key is required to verify "
                "promoted artifacts"
            ),
            operation="scoring_model_resolve",
            status_code=500,
            hint="Configure MODEL_SIGNING_PUBLIC_KEY_PEM for scoring-service",
        )
    return public_key_pem


def _require_non_empty_str(record: Any, field_name: str) -> str:
    value = record[field_name]
    if not isinstance(value, str) or value.strip() == "":
        raise ServiceError(
            error_code="SCORING_MODEL_REGISTRY_INVALID",
            message="Scoring model registry row contains an invalid string field",
            operation="scoring_model_resolve",
            status_code=500,
            cause=field_name,
        )
    return value


def _raise_artifact_store_error(exc: ArtifactStoreError) -> NoReturn:
    if exc.kind == "invalid_uri":
        raise ServiceError(
            error_code="SCORING_MODEL_ARTIFACT_PATH_INVALID",
            message="Promoted scoring model artifact escapes configured storage boundary",
            operation="scoring_model_resolve",
            status_code=400,
            cause=exc.detail,
        ) from exc
    if exc.kind == "not_found":
        raise ServiceError(
            error_code="SCORING_MODEL_ARTIFACT_MISSING",
            message="Promoted scoring model artifact was not found in artifact storage",
            operation="scoring_model_resolve",
            status_code=500,
            cause=exc.detail,
        ) from exc
    raise ServiceError(
        error_code="SCORING_MODEL_ARTIFACT_STORAGE_FAILURE",
        message="Artifact storage backend failed while loading promoted scoring model",
        operation="scoring_model_resolve",
        status_code=500,
        cause=exc.detail,
    ) from exc
