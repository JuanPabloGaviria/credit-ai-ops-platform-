import pytest

from contracts import AssistantSummaryRequest
from shared_kernel import summarize_case


@pytest.mark.unit
def test_assistant_summary_is_deterministic() -> None:
    request = AssistantSummaryRequest(
        application_id="app-000001",
        decision="review",
        risk_score=0.55,
        reason_codes=["HIGH_DTI", "SHORT_HISTORY"],
    )

    first = summarize_case(request)
    second = summarize_case(request)

    assert first.mode == "deterministic"
    assert first.summary == second.summary
