"""Deterministic collaborator-assistant summarization."""

from __future__ import annotations

from contracts import AssistantSummaryRequest, AssistantSummaryResponse


def summarize_case(request: AssistantSummaryRequest) -> AssistantSummaryResponse:
    """Generate deterministic analyst summary without external LLM dependency."""
    reason_text = ", ".join(request.reason_codes)
    summary = (
        f"Application {request.application_id} is marked '{request.decision}' "
        f"with risk score {request.risk_score:.2f}. "
        f"Primary reason codes: {reason_text}."
    )
    return AssistantSummaryResponse(
        application_id=request.application_id,
        summary=summary,
        mode="deterministic",
    )
