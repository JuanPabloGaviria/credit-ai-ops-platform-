import pytest

from contracts import ApplicationInput, DecisionRequest, FeatureVector
from shared_kernel import decide_credit, materialize_features, score_application


@pytest.mark.unit
def test_credit_policy_end_to_end_deterministic() -> None:
    application = ApplicationInput(
        application_id="app-000001",
        applicant_id="applicant-001",
        monthly_income=4000,
        monthly_debt=1200,
        requested_amount=18000,
        credit_history_months=24,
        existing_defaults=0,
    )

    features = materialize_features(application)
    score = score_application(features)
    decision = decide_credit(
        DecisionRequest(
            application_id=application.application_id,
            risk_score=score.risk_score,
            requested_amount=application.requested_amount,
            reason_codes=score.reason_codes,
        )
    )

    assert abs(features.debt_to_income - 0.3) < 1e-9
    assert 0 <= score.risk_score <= 1
    assert decision.decision in {"approve", "review", "decline"}


@pytest.mark.unit
def test_score_application_bounds_high_risk_profile_to_one() -> None:
    features = FeatureVector(
        application_id="app-risk-high",
        requested_amount=30000.0,
        debt_to_income=0.70,
        amount_to_income=0.80,
        credit_history_months=6,
        existing_defaults=2,
    )

    score = score_application(features)

    assert score.risk_score == 1.0
    assert set(score.reason_codes) == {
        "HIGH_DTI",
        "HIGH_REQUEST_RATIO",
        "SHORT_HISTORY",
        "PRIOR_DEFAULT",
    }


@pytest.mark.unit
def test_score_application_sets_low_risk_reason_when_no_risk_flags() -> None:
    features = FeatureVector(
        application_id="app-risk-low",
        requested_amount=10000.0,
        debt_to_income=0.20,
        amount_to_income=0.10,
        credit_history_months=48,
        existing_defaults=0,
    )

    score = score_application(features)

    assert score.reason_codes == ["LOW_RISK_PROFILE"]
    assert score.risk_score == 0.15


@pytest.mark.unit
@pytest.mark.parametrize(
    ("risk_score", "expected_decision", "expected_policy_code"),
    [
        (0.70, "decline", "POLICY_DECLINE_THRESHOLD"),
        (0.45, "review", "POLICY_MANUAL_REVIEW"),
        (0.44, "approve", "POLICY_AUTO_APPROVE"),
    ],
)
def test_decide_credit_thresholds(
    risk_score: float,
    expected_decision: str,
    expected_policy_code: str,
) -> None:
    result = decide_credit(
        DecisionRequest(
            application_id="app-threshold",
            risk_score=risk_score,
            requested_amount=20000.0,
            reason_codes=["BASE_REASON"],
        )
    )

    assert result.decision == expected_decision
    assert result.reason_codes[-1] == expected_policy_code
