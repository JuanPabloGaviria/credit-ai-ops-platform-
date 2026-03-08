"""Core typed domain models used across service contracts."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

_MODEL_COMPONENT_PATTERN = r"^[a-z0-9][a-z0-9._-]{2,63}$"
_SPEC_REFERENCE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{2,127}$"


class ApplicationInput(BaseModel):
    """Credit application payload accepted from gateway/application services."""

    model_config = ConfigDict(extra="forbid")

    application_id: str = Field(min_length=8)
    applicant_id: str = Field(min_length=8)
    monthly_income: float = Field(gt=0)
    monthly_debt: float = Field(ge=0)
    requested_amount: float = Field(gt=0)
    credit_history_months: int = Field(ge=0)
    existing_defaults: int = Field(ge=0)


class FeatureVector(BaseModel):
    """Feature materialization output for model inference."""

    model_config = ConfigDict(extra="forbid")

    application_id: str = Field(min_length=8)
    requested_amount: float = Field(gt=0)
    debt_to_income: float = Field(ge=0)
    amount_to_income: float = Field(ge=0)
    credit_history_months: int = Field(ge=0)
    existing_defaults: int = Field(ge=0)


class ScorePrediction(BaseModel):
    """Model inference result with reason codes."""

    model_config = ConfigDict(extra="forbid")

    application_id: str = Field(min_length=8)
    requested_amount: float = Field(gt=0)
    risk_score: float = Field(ge=0, le=1)
    model_version: str = Field(min_length=3)
    reason_codes: list[str]


class DecisionRequest(BaseModel):
    """Decision engine input using score + context."""

    model_config = ConfigDict(extra="forbid")

    application_id: str = Field(min_length=8)
    risk_score: float = Field(ge=0, le=1)
    requested_amount: float = Field(gt=0)
    reason_codes: list[str]


class DecisionResult(BaseModel):
    """Hybrid policy decision output."""

    model_config = ConfigDict(extra="forbid")

    application_id: str = Field(min_length=8)
    risk_score: float = Field(ge=0, le=1)
    decision: str = Field(pattern=r"^(approve|review|decline)$")
    reason_codes: list[str]


class AssistantSummaryRequest(BaseModel):
    """Case summary request for collaborator assistant."""

    model_config = ConfigDict(extra="forbid")

    application_id: str = Field(min_length=8)
    decision: str = Field(pattern=r"^(approve|review|decline)$")
    risk_score: float = Field(ge=0, le=1)
    reason_codes: list[str]


class AssistantSummaryResponse(BaseModel):
    """Deterministic summary response."""

    model_config = ConfigDict(extra="forbid")

    application_id: str
    summary: str
    mode: str = Field(pattern=r"^(deterministic|llm)$")


class GatewayCreditEvaluationResponse(BaseModel):
    """Synchronous gateway response across feature, scoring, and decision services."""

    model_config = ConfigDict(extra="forbid")

    features: FeatureVector
    score: ScorePrediction
    decision: DecisionResult


class ModelMetadata(BaseModel):
    """Reproducibility metadata for model artifacts."""

    model_config = ConfigDict(extra="forbid")

    model_name: str = Field(pattern=_MODEL_COMPONENT_PATTERN)
    model_version: str = Field(pattern=_MODEL_COMPONENT_PATTERN)
    dataset_hash: str = Field(min_length=16)
    random_seed: int = Field(ge=0)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    environment_fingerprint: str = Field(min_length=3)


class TrainingMetrics(BaseModel):
    """Deterministic training/evaluation metric set for governance checks."""

    model_config = ConfigDict(extra="forbid")

    auc: float = Field(ge=0, le=1)
    precision_at_50: float = Field(ge=0, le=1)
    recall_at_50: float = Field(ge=0, le=1)
    calibration_error: float = Field(ge=0, le=1)


class TrainRunRequest(BaseModel):
    """Training request for deterministic model build."""

    model_config = ConfigDict(extra="forbid")

    model_name: str = Field(pattern=_MODEL_COMPONENT_PATTERN)
    dataset_reference: str = Field(min_length=3)
    random_seed: int = Field(ge=0)
    algorithm: str = Field(
        default="sklearn_logistic_regression",
        pattern=r"^sklearn_logistic_regression$",
    )
    feature_spec_ref: str = Field(
        default="credit-feature-spec/v1",
        pattern=_SPEC_REFERENCE_PATTERN,
    )
    training_spec_ref: str = Field(
        default="credit-training-spec/v1",
        pattern=_SPEC_REFERENCE_PATTERN,
    )


class TrainRunResponse(BaseModel):
    """Training output with artifact location and reproducibility metadata."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=8)
    model_name: str = Field(pattern=_MODEL_COMPONENT_PATTERN)
    dataset_hash: str = Field(min_length=16)
    random_seed: int = Field(ge=0)
    algorithm: str = Field(pattern=r"^sklearn_logistic_regression$")
    feature_spec_ref: str = Field(pattern=_SPEC_REFERENCE_PATTERN)
    training_spec_ref: str = Field(pattern=_SPEC_REFERENCE_PATTERN)
    artifact_uri: str = Field(min_length=3)
    artifact_digest: str = Field(min_length=16)
    metrics: TrainingMetrics


class EvaluateRunRequest(BaseModel):
    """Evaluation request for a completed training run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=8)


class EvaluateRunResponse(BaseModel):
    """Evaluation result and policy gate status."""

    model_config = ConfigDict(extra="forbid")

    evaluation_id: str = Field(min_length=8)
    run_id: str = Field(min_length=8)
    metrics: TrainingMetrics
    passed_policy: bool
    policy_failures: list[str]


class RegisterModelRequest(BaseModel):
    """Registry request for evaluated model candidate."""

    model_config = ConfigDict(extra="forbid")

    model_name: str = Field(pattern=_MODEL_COMPONENT_PATTERN)
    model_version: str = Field(pattern=_MODEL_COMPONENT_PATTERN)
    run_id: str = Field(min_length=8)
    evaluation_id: str = Field(min_length=8)
    feature_spec_ref: str = Field(default="credit-feature-spec/v1", pattern=_SPEC_REFERENCE_PATTERN)
    training_spec_ref: str = Field(
        default="credit-training-spec/v1",
        pattern=_SPEC_REFERENCE_PATTERN,
    )


class RegisterModelResponse(BaseModel):
    """Registry response including generated model metadata and card location."""

    model_config = ConfigDict(extra="forbid")

    model_name: str = Field(pattern=_MODEL_COMPONENT_PATTERN)
    model_version: str = Field(pattern=_MODEL_COMPONENT_PATTERN)
    status: str = Field(pattern=r"^(candidate|promoted)$")
    model_card_uri: str = Field(min_length=3)
    metadata: ModelMetadata


class PromoteModelRequest(BaseModel):
    """Promotion request for an existing registry candidate."""

    model_config = ConfigDict(extra="forbid")

    model_name: str = Field(pattern=_MODEL_COMPONENT_PATTERN)
    model_version: str = Field(pattern=_MODEL_COMPONENT_PATTERN)
    stage: str = Field(pattern=r"^(staging|production)$")
    approved_by: str = Field(min_length=3, max_length=128)
    approval_ticket: str = Field(min_length=3, max_length=128)
    risk_signoff_ref: str = Field(min_length=3, max_length=128)


class PromoteModelResponse(BaseModel):
    """Promotion response with emitted event identifier."""

    model_config = ConfigDict(extra="forbid")

    model_name: str = Field(pattern=_MODEL_COMPONENT_PATTERN)
    model_version: str = Field(pattern=_MODEL_COMPONENT_PATTERN)
    stage: str = Field(pattern=r"^(staging|production)$")
    promoted_at: datetime
    event_id: str = Field(min_length=8)


class MLOpsRunResponse(BaseModel):
    """Run detail payload used by GET run endpoint."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=8)
    model_name: str = Field(pattern=_MODEL_COMPONENT_PATTERN)
    dataset_hash: str = Field(min_length=16)
    random_seed: int = Field(ge=0)
    algorithm: str = Field(pattern=r"^sklearn_logistic_regression$")
    feature_spec_ref: str = Field(pattern=_SPEC_REFERENCE_PATTERN)
    training_spec_ref: str = Field(pattern=_SPEC_REFERENCE_PATTERN)
    artifact_uri: str = Field(min_length=3)
    artifact_digest: str = Field(min_length=16)
    status: str = Field(pattern=r"^(succeeded|failed)$")
    metrics: TrainingMetrics
    created_at: datetime
    completed_at: datetime
