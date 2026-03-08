"""Deterministic feature, scoring, and decision policy helpers."""

from __future__ import annotations

from dataclasses import dataclass

from contracts import (
    ApplicationInput,
    DecisionRequest,
    DecisionResult,
    FeatureVector,
    ScorePrediction,
)


@dataclass(frozen=True, slots=True)
class CreditPolicyConfig:
    """Explicit policy coefficients and thresholds for deterministic scoring."""

    model_version: str = "baseline_lr_v1"
    baseline_risk_score: float = 0.15
    high_dti_threshold: float = 0.45
    high_dti_penalty: float = 0.35
    high_request_ratio_threshold: float = 0.35
    high_request_ratio_penalty: float = 0.25
    short_history_months_threshold: int = 18
    short_history_penalty: float = 0.20
    prior_default_penalty: float = 0.30
    decline_threshold: float = 0.70
    review_threshold: float = 0.45

    def __post_init__(self) -> None:
        bounded_values = (
            self.baseline_risk_score,
            self.high_dti_threshold,
            self.high_dti_penalty,
            self.high_request_ratio_threshold,
            self.high_request_ratio_penalty,
            self.short_history_penalty,
            self.prior_default_penalty,
            self.decline_threshold,
            self.review_threshold,
        )
        if any(value < 0 or value > 1 for value in bounded_values):
            raise ValueError("credit policy bounded values must remain within [0, 1]")
        if self.short_history_months_threshold < 0:
            raise ValueError("short_history_months_threshold must be non-negative")
        if self.review_threshold > self.decline_threshold:
            raise ValueError("review_threshold cannot be greater than decline_threshold")


DEFAULT_CREDIT_POLICY = CreditPolicyConfig()
MODEL_VERSION = DEFAULT_CREDIT_POLICY.model_version


def materialize_features(application: ApplicationInput) -> FeatureVector:
    """Create deterministic feature vector from validated application input."""
    debt_to_income = application.monthly_debt / application.monthly_income
    amount_to_income = application.requested_amount / (application.monthly_income * 12)
    return FeatureVector(
        application_id=application.application_id,
        requested_amount=application.requested_amount,
        debt_to_income=debt_to_income,
        amount_to_income=amount_to_income,
        credit_history_months=application.credit_history_months,
        existing_defaults=application.existing_defaults,
    )


def score_application(
    features: FeatureVector,
    *,
    policy: CreditPolicyConfig = DEFAULT_CREDIT_POLICY,
) -> ScorePrediction:
    """Compute deterministic baseline risk score and reason codes."""
    risk_score = policy.baseline_risk_score
    reason_codes: list[str] = []

    if features.debt_to_income > policy.high_dti_threshold:
        risk_score += policy.high_dti_penalty
        reason_codes.append("HIGH_DTI")
    if features.amount_to_income > policy.high_request_ratio_threshold:
        risk_score += policy.high_request_ratio_penalty
        reason_codes.append("HIGH_REQUEST_RATIO")
    if features.credit_history_months < policy.short_history_months_threshold:
        risk_score += policy.short_history_penalty
        reason_codes.append("SHORT_HISTORY")
    if features.existing_defaults > 0:
        risk_score += policy.prior_default_penalty
        reason_codes.append("PRIOR_DEFAULT")

    bounded_score = min(max(risk_score, 0.0), 1.0)
    if not reason_codes:
        reason_codes.append("LOW_RISK_PROFILE")

    return ScorePrediction(
        application_id=features.application_id,
        requested_amount=features.requested_amount,
        risk_score=bounded_score,
        model_version=policy.model_version,
        reason_codes=reason_codes,
    )


def decide_credit(
    request: DecisionRequest,
    *,
    policy: CreditPolicyConfig = DEFAULT_CREDIT_POLICY,
) -> DecisionResult:
    """Hybrid policy decision with deterministic reason-code handling."""
    if request.risk_score >= policy.decline_threshold:
        decision = "decline"
        reason_codes = [*request.reason_codes, "POLICY_DECLINE_THRESHOLD"]
    elif request.risk_score >= policy.review_threshold:
        decision = "review"
        reason_codes = [*request.reason_codes, "POLICY_MANUAL_REVIEW"]
    else:
        decision = "approve"
        reason_codes = [*request.reason_codes, "POLICY_AUTO_APPROVE"]

    return DecisionResult(
        application_id=request.application_id,
        risk_score=request.risk_score,
        decision=decision,
        reason_codes=reason_codes,
    )
