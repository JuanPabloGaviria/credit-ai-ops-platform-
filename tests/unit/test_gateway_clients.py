from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any, ClassVar

import httpx
import pytest
from api_gateway.clients import GatewayPipelineClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from shared_kernel import (
    ServiceSettings,
    configure_telemetry,
    force_flush_telemetry,
    get_tracer,
    observability_context,
    shutdown_telemetry,
)
from tests.defaults import DEFAULT_POSTGRES_DSN, DEFAULT_RABBITMQ_URL


class _FakeAsyncClient:
    captured_headers: ClassVar[list[dict[str, str]]] = []

    def __init__(self, *, base_url: str, timeout: httpx.Timeout) -> None:
        _ = timeout
        self._base_url = base_url

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        return None

    async def post(
        self,
        path: str,
        *,
        json: Mapping[str, Any],
        headers: Mapping[str, str],
    ) -> httpx.Response:
        _ = json
        _FakeAsyncClient.captured_headers.append(dict(headers))
        if self._base_url.endswith("feature.internal"):
            return httpx.Response(
                200,
                json={
                    "application_id": "app-0001",
                    "requested_amount": 20000,
                    "debt_to_income": 0.36,
                    "amount_to_income": 0.33,
                    "credit_history_months": 36,
                    "existing_defaults": 0,
                },
                request=httpx.Request("POST", f"{self._base_url}{path}"),
            )
        if self._base_url.endswith("scoring.internal"):
            return httpx.Response(
                200,
                json={
                    "application_id": "app-0001",
                    "requested_amount": 20000,
                    "risk_score": 0.31,
                    "model_version": "baseline_lr_v1",
                    "reason_codes": ["LOW_RISK_PROFILE"],
                },
                request=httpx.Request("POST", f"{self._base_url}{path}"),
            )
        return httpx.Response(
            200,
            json={
                "application_id": "app-0001",
                "risk_score": 0.31,
                "decision": "approve",
                "reason_codes": ["LOW_RISK_PROFILE", "POLICY_AUTO_APPROVE"],
            },
            request=httpx.Request("POST", f"{self._base_url}{path}"),
        )


def _settings() -> ServiceSettings:
    return ServiceSettings(
        service_name="api-gateway",
        postgres_dsn=DEFAULT_POSTGRES_DSN,
        rabbitmq_url=DEFAULT_RABBITMQ_URL,
        feature_service_url="https://feature.internal",
        scoring_service_url="https://scoring.internal",
        decision_service_url="https://decision.internal",
    )


@pytest.mark.unit
def test_gateway_pipeline_propagates_observability_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async def _fake_authorization(_: ServiceSettings) -> str:
            return "Bearer service-token"

        monkeypatch.setattr("api_gateway.clients.httpx.AsyncClient", _FakeAsyncClient)
        monkeypatch.setattr("api_gateway.clients.build_service_authorization", _fake_authorization)
        _FakeAsyncClient.captured_headers.clear()
        settings = _settings()
        configure_telemetry(
            ServiceSettings.model_validate(
                {
                    **settings.model_dump(mode="python"),
                    "otel_enabled": True,
                    "otel_exporter_otlp_endpoint": "http://collector.internal:4318/v1/traces",
                }
            ),
            span_exporter_factory=InMemorySpanExporter,
        )
        client = GatewayPipelineClient(settings)

        try:
            with get_tracer("tests.gateway").start_as_current_span("gateway-request"):
                with observability_context(
                    trace_id="trace-gateway-0001",
                    correlation_id="corr-gateway-0001",
                    causation_id="cause-gateway-0001",
                ):
                    _ = await client.evaluate_credit(
                        application_payload={
                            "application_id": "app-0001",
                            "requested_amount": 20000,
                        },
                        trace_id="trace-gateway-0001",
                    )
            force_flush_telemetry()
        finally:
            shutdown_telemetry()

        assert len(_FakeAsyncClient.captured_headers) == 3
        for headers in _FakeAsyncClient.captured_headers:
            assert headers["x-trace-id"] == "trace-gateway-0001"
            assert headers["x-correlation-id"] == "corr-gateway-0001"
            assert headers["x-causation-id"] == "cause-gateway-0001"
            assert headers["Authorization"] == "Bearer service-token"
            assert headers["traceparent"].startswith("00-")

    asyncio.run(scenario())
