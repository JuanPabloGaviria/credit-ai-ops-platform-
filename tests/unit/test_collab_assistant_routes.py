from __future__ import annotations

import collab_assistant.routes as assistant_routes
import pytest
from collab_assistant.main import app
from fastapi.testclient import TestClient

from contracts import AssistantSummaryRequest, AssistantSummaryResponse
from shared_kernel import ServiceError


class _FakeAssistantRepository:
    def __init__(self) -> None:
        self.connected = False
        self.closed = False
        self.last_summary_request: AssistantSummaryRequest | None = None
        self.last_trace_id: str | None = None

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.closed = True

    async def summarize_request(
        self,
        request: AssistantSummaryRequest,
        trace_id: str,
    ) -> AssistantSummaryResponse:
        self.last_summary_request = request
        self.last_trace_id = trace_id
        return AssistantSummaryResponse(
            application_id=request.application_id,
            summary="deterministic summary",
            mode="deterministic",
        )

    async def get_summary(self, application_id: str) -> AssistantSummaryResponse:
        if application_id == "app-missing":
            raise ServiceError(
                error_code="ASSISTANT_SUMMARY_NOT_FOUND",
                message="summary missing",
                operation="assistant_get_summary",
                status_code=404,
            )
        return AssistantSummaryResponse(
            application_id=application_id,
            summary="persisted summary",
            mode="deterministic",
        )


def _install_fake_repository(
    monkeypatch: pytest.MonkeyPatch,
    fake_repository: _FakeAssistantRepository,
) -> None:
    def _factory(settings: object) -> _FakeAssistantRepository:
        _ = settings
        return fake_repository

    monkeypatch.setattr(assistant_routes, "AssistantRepository", _factory)


@pytest.mark.unit
def test_assistant_summarize_persists_and_queues(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_repository = _FakeAssistantRepository()
    _install_fake_repository(monkeypatch, fake_repository)
    client = TestClient(app)

    response = client.post(
        "/v1/assistant/summarize",
        json={
            "application_id": "app-12345678",
            "decision": "review",
            "risk_score": 0.52,
            "reason_codes": ["HIGH_DTI"],
        },
        headers={"x-idempotency-key": "assistant-idem-0001"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["application_id"] == "app-12345678"
    assert payload["mode"] == "deterministic"
    assert fake_repository.connected is True
    assert fake_repository.closed is True
    assert fake_repository.last_summary_request is not None
    assert fake_repository.last_trace_id is not None


@pytest.mark.unit
def test_assistant_get_summary_returns_persisted_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_repository = _FakeAssistantRepository()
    _install_fake_repository(monkeypatch, fake_repository)
    client = TestClient(app)

    response = client.get(
        "/v1/assistant/summaries/app-12345678",
        headers={"x-idempotency-key": "assistant-idem-0002"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "application_id": "app-12345678",
        "summary": "persisted summary",
        "mode": "deterministic",
    }


@pytest.mark.unit
def test_assistant_get_summary_returns_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_repository = _FakeAssistantRepository()
    _install_fake_repository(monkeypatch, fake_repository)
    client = TestClient(app)

    response = client.get(
        "/v1/assistant/summaries/app-missing",
        headers={"x-idempotency-key": "assistant-idem-0003"},
    )

    assert response.status_code == 404
    assert response.json()["error_code"] == "ASSISTANT_SUMMARY_NOT_FOUND"


@pytest.mark.unit
def test_assistant_rejects_short_idempotency_key() -> None:
    client = TestClient(app)

    response = client.get(
        "/v1/assistant/status",
        headers={"x-idempotency-key": "short"},
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "INVALID_IDEMPOTENCY_KEY"
