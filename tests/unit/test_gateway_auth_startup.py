from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, cast

import api_gateway.auth as gateway_auth
import pytest

import shared_kernel.auth as shared_auth
from shared_kernel import ServiceError, ServiceSettings
from tests.defaults import DEFAULT_POSTGRES_DSN, DEFAULT_RABBITMQ_URL


def _base_settings(**overrides: Any) -> ServiceSettings:
    service_token_url = "/".join(("https://issuer.example.test", "oauth", "token"))
    settings = ServiceSettings(
        service_name="api-gateway",
        postgres_dsn=DEFAULT_POSTGRES_DSN,
        rabbitmq_url=DEFAULT_RABBITMQ_URL,
        auth_mode="required",
        auth_jwks_url="https://issuer.example.test/.well-known/jwks.json",
        auth_issuer="https://issuer.example.test",
        auth_service_token_url=service_token_url,
        auth_service_client_id="api-gateway",
        auth_service_client_secret="-".join(("not", "real", "secret", "value")),
    )
    return settings.model_copy(update=overrides)


@pytest.mark.unit
def test_gateway_auth_startup_checks_disabled_mode_returns_empty_list() -> None:
    settings = _base_settings(auth_mode="disabled")
    checks = gateway_auth.build_gateway_auth_startup_checks(settings)
    assert checks == []


@pytest.mark.unit
def test_gateway_auth_startup_checks_fail_on_missing_service_credentials() -> None:
    settings = _base_settings(auth_service_client_secret=None)
    checks = gateway_auth.build_gateway_auth_startup_checks(settings)
    assert len(checks) == 3

    with pytest.raises(ServiceError) as error:
        third_check = cast(Coroutine[Any, Any, None], checks[2]())
        asyncio.run(third_check)

    assert error.value.error_code == "AUTH_CONFIG_INVALID"


@pytest.mark.unit
def test_gateway_auth_startup_checks_fail_on_invalid_provider_url() -> None:
    settings = _base_settings(auth_jwks_url="not-a-url")
    checks = gateway_auth.build_gateway_auth_startup_checks(settings)
    assert len(checks) == 3

    with pytest.raises(ServiceError) as error:
        second_check = cast(Coroutine[Any, Any, None], checks[1]())
        asyncio.run(second_check)

    assert error.value.error_code == "AUTH_PROVIDER_INVALID_URL"


@pytest.mark.unit
def test_gateway_auth_startup_checks_probe_provider_when_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, float]] = []

    async def fake_probe(url: str, *, operation: str, timeout_seconds: float) -> None:
        calls.append((url, operation, timeout_seconds))

    monkeypatch.setattr(shared_auth, "_probe_endpoint", fake_probe)
    settings = _base_settings()
    checks = gateway_auth.build_gateway_auth_startup_checks(settings)

    first_check = cast(Coroutine[Any, Any, None], checks[0]())
    second_check = cast(Coroutine[Any, Any, None], checks[1]())
    third_check = cast(Coroutine[Any, Any, None], checks[2]())
    asyncio.run(first_check)
    asyncio.run(second_check)
    asyncio.run(third_check)

    assert calls == [
        (
            "https://issuer.example.test/.well-known/jwks.json",
            "auth_jwks_probe",
            settings.startup_probe_timeout_seconds,
        ),
        (
            "https://issuer.example.test/oauth/token",
            "auth_service_token_probe",
            settings.startup_probe_timeout_seconds,
        ),
    ]
