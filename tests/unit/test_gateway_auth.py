from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import api_gateway.auth as gateway_auth
import jwt
import pytest

from shared_kernel import ServiceError, ServiceSettings
from tests.defaults import DEFAULT_POSTGRES_DSN, DEFAULT_RABBITMQ_URL

_AUTH_SHARED_SECRET = "-".join(("test", "shared", "secret", "00000001"))
_AUTH_ISSUER = "https://issuer.example.test"


def _required_auth_settings(
    *,
    required_scope: str | None = None,
    required_audience: str | None = None,
) -> ServiceSettings:
    return ServiceSettings(
        service_name="api-gateway",
        postgres_dsn=DEFAULT_POSTGRES_DSN,
        rabbitmq_url=DEFAULT_RABBITMQ_URL,
        auth_mode="required",
        auth_shared_secret=_AUTH_SHARED_SECRET,
        auth_issuer=_AUTH_ISSUER,
        auth_required_scope=required_scope,
        auth_required_audience=required_audience,
    )


def _bearer_token(
    *,
    secret: str = _AUTH_SHARED_SECRET,
    sub: str = "user-123",
    scope: str | list[str] | None = None,
    audience: str | list[Any] | None = None,
    expires_delta: timedelta = timedelta(minutes=5),
) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "iss": _AUTH_ISSUER,
        "sub": sub,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + expires_delta).timestamp()),
    }
    if scope is not None:
        payload["scope"] = scope
    if audience is not None:
        payload["aud"] = audience
    encoded = jwt.encode(payload, secret, algorithm="HS256")
    return f"Bearer {encoded}"


@pytest.mark.unit
def test_authorize_gateway_request_rejects_missing_header() -> None:
    settings = _required_auth_settings()

    with pytest.raises(ServiceError) as error:
        asyncio.run(
            gateway_auth.authorize_gateway_request(
                settings=settings,
                authorization=None,
                operation="credit_evaluate",
            )
        )

    assert error.value.error_code == "AUTH_MISSING_BEARER_TOKEN"


@pytest.mark.unit
def test_authorize_gateway_request_rejects_non_bearer_scheme() -> None:
    settings = _required_auth_settings()

    with pytest.raises(ServiceError) as error:
        asyncio.run(
            gateway_auth.authorize_gateway_request(
                settings=settings,
                authorization="Basic abc12345",
                operation="credit_evaluate",
            )
        )

    assert error.value.error_code == "AUTH_INVALID_AUTH_SCHEME"


@pytest.mark.unit
def test_authorize_gateway_request_rejects_invalid_signature() -> None:
    settings = _required_auth_settings()

    with pytest.raises(ServiceError) as error:
        asyncio.run(
            gateway_auth.authorize_gateway_request(
                settings=settings,
                authorization=_bearer_token(secret="-".join(("wrong", "shared", "secret"))),
                operation="credit_evaluate",
            )
        )

    assert error.value.error_code == "AUTH_INVALID_TOKEN"


@pytest.mark.unit
def test_authorize_gateway_request_rejects_missing_required_scope() -> None:
    settings = _required_auth_settings(required_scope="credit_manager")

    with pytest.raises(ServiceError) as error:
        asyncio.run(
            gateway_auth.authorize_gateway_request(
                settings=settings,
                authorization=_bearer_token(scope="credit_analyst"),
                operation="credit_evaluate",
            )
        )

    assert error.value.error_code == "AUTH_FORBIDDEN_SCOPE"


@pytest.mark.unit
def test_authorize_gateway_request_rejects_missing_required_audience() -> None:
    settings = _required_auth_settings(required_audience="api-gateway")

    with pytest.raises(ServiceError) as error:
        asyncio.run(
            gateway_auth.authorize_gateway_request(
                settings=settings,
                authorization=_bearer_token(audience="gateway-other"),
                operation="credit_evaluate",
            )
        )

    assert error.value.error_code == "AUTH_FORBIDDEN_AUDIENCE"


@pytest.mark.unit
def test_authorize_gateway_request_accepts_required_audience_as_string() -> None:
    settings = _required_auth_settings(required_audience="api-gateway")

    principal = asyncio.run(
        gateway_auth.authorize_gateway_request(
            settings=settings,
            authorization=_bearer_token(audience="api-gateway"),
            operation="credit_evaluate",
        )
    )

    assert principal.subject == "user-123"


@pytest.mark.unit
def test_authorize_gateway_request_accepts_required_audience_in_list() -> None:
    settings = _required_auth_settings(required_audience="api-gateway")

    principal = asyncio.run(
        gateway_auth.authorize_gateway_request(
            settings=settings,
            authorization=_bearer_token(audience=["api-gateway", "credit-platform"]),
            operation="credit_evaluate",
        )
    )

    assert "api-gateway" in principal.audiences


@pytest.mark.unit
def test_authorize_gateway_request_rejects_malformed_audience_claim() -> None:
    settings = _required_auth_settings(required_audience="api-gateway")

    with pytest.raises(ServiceError) as error:
        asyncio.run(
            gateway_auth.authorize_gateway_request(
                settings=settings,
                authorization=_bearer_token(audience=["api-gateway", 123]),
                operation="credit_evaluate",
            )
        )

    assert error.value.error_code == "AUTH_PROVIDER_RESPONSE_INVALID"


@pytest.mark.unit
def test_authorize_gateway_request_accepts_active_scoped_token() -> None:
    settings = _required_auth_settings(required_scope="credit_manager")

    principal = asyncio.run(
        gateway_auth.authorize_gateway_request(
            settings=settings,
            authorization=_bearer_token(scope="credit_analyst credit_manager"),
            operation="credit_evaluate",
        )
    )

    assert "credit_manager" in principal.scopes
