"""Shared JWT authorization and service-token helpers."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast
from urllib.parse import urlparse

import httpx
import jwt
from jwt import PyJWKClient
from jwt.exceptions import InvalidTokenError, PyJWKClientConnectionError

from .config import ServiceSettings
from .dependencies import ProbeCallable
from .errors import ServiceError

ClaimSet = Mapping[str, object]


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """Normalized authenticated principal extracted from bearer token claims."""

    subject: str
    scopes: frozenset[str]
    audiences: frozenset[str]
    claims: ClaimSet


@dataclass(frozen=True, slots=True)
class _ServiceTokenCacheKey:
    token_url: str
    client_id: str
    scope: str | None
    audience: str | None


@dataclass(slots=True)
class _CachedServiceToken:
    access_token: str
    expires_at_monotonic: float


_JWKS_CLIENTS: dict[str, PyJWKClient] = {}
_SERVICE_TOKENS: dict[_ServiceTokenCacheKey, _CachedServiceToken] = {}
_SERVICE_TOKEN_LOCKS: dict[_ServiceTokenCacheKey, asyncio.Lock] = {}


def extract_bearer_token(authorization: str | None, operation: str) -> str:
    """Validate Authorization header and extract bearer token."""
    if authorization is None:
        raise ServiceError(
            error_code="AUTH_MISSING_BEARER_TOKEN",
            message="Authorization header is required for this endpoint",
            operation=operation,
            status_code=401,
            hint="Pass Authorization: Bearer <token>",
        )
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        raise ServiceError(
            error_code="AUTH_INVALID_AUTH_SCHEME",
            message="Authorization scheme must be Bearer",
            operation=operation,
            status_code=401,
            hint="Use Authorization: Bearer <token>",
        )
    normalized_token = token.strip()
    if len(normalized_token) < 8:
        raise ServiceError(
            error_code="AUTH_INVALID_TOKEN",
            message="Bearer token is missing or too short",
            operation=operation,
            status_code=401,
            hint="Provide a valid access token",
        )
    return normalized_token


async def authorize_request(
    *,
    settings: ServiceSettings,
    authorization: str | None,
    operation: str,
) -> AuthenticatedPrincipal:
    """Authorize inbound bearer token according to service settings."""
    if settings.auth_mode == "disabled":
        return AuthenticatedPrincipal(
            subject="anonymous",
            scopes=frozenset(),
            audiences=frozenset(),
            claims={},
        )

    token = extract_bearer_token(authorization, operation)
    claims = await _decode_token(settings=settings, token=token, operation=operation)
    principal = _build_principal(claims=claims, operation=operation)
    _enforce_audience(principal, settings=settings, operation=operation)
    _enforce_scope(principal, settings=settings, operation=operation)
    return principal


async def build_service_authorization(settings: ServiceSettings) -> str | None:
    """Build outbound service-to-service Authorization header when auth is enabled."""
    if settings.auth_mode == "disabled":
        return None

    shared_secret = _normalized(settings.auth_shared_secret)
    if shared_secret is not None:
        client_id = _required_setting(
            _normalized(settings.auth_service_client_id) or settings.service_name,
            error_code="AUTH_CONFIG_INVALID",
            message="Service client id is required for shared-secret service tokens",
            operation="service_auth_config",
        )
        audience = _normalized(settings.auth_service_audience) or _normalized(
            settings.auth_required_audience
        )
        token = jwt.encode(
            payload={
                "iss": _normalized(settings.auth_issuer) or "local-service-auth",
                "sub": client_id,
                "aud": audience,
                "scope": _normalized(settings.auth_service_scope),
                "iat": int(time.time()),
                "nbf": int(time.time()),
                "exp": int(time.time()) + 300,
            },
            key=shared_secret,
            algorithm="HS256",
        )
        return f"Bearer {token}"

    token_url = _required_setting(
        _normalized(settings.auth_service_token_url),
        error_code="AUTH_CONFIG_INVALID",
        message="Service token URL is required when auth is enabled",
        operation="service_auth_config",
    )
    client_id = _required_setting(
        _normalized(settings.auth_service_client_id),
        error_code="AUTH_CONFIG_INVALID",
        message="Service client id is required when auth is enabled",
        operation="service_auth_config",
    )
    client_secret = _required_setting(
        _normalized(settings.auth_service_client_secret),
        error_code="AUTH_CONFIG_INVALID",
        message="Service client secret is required when auth is enabled",
        operation="service_auth_config",
    )
    scope = _normalized(settings.auth_service_scope)
    audience = _normalized(settings.auth_service_audience)

    cache_key = _ServiceTokenCacheKey(
        token_url=token_url,
        client_id=client_id,
        scope=scope,
        audience=audience,
    )
    cached = _SERVICE_TOKENS.get(cache_key)
    if cached is not None and cached.expires_at_monotonic > (time.monotonic() + 30.0):
        return f"Bearer {cached.access_token}"

    lock = _SERVICE_TOKEN_LOCKS.setdefault(cache_key, asyncio.Lock())
    async with lock:
        cached = _SERVICE_TOKENS.get(cache_key)
        if cached is not None and cached.expires_at_monotonic > (time.monotonic() + 30.0):
            return f"Bearer {cached.access_token}"
        access_token, expires_in_seconds = await _request_service_token(
            token_url=token_url,
            client_id=client_id,
            client_secret=client_secret,
            scope=scope,
            audience=audience,
            timeout_seconds=settings.keycloak_request_timeout_seconds,
        )
        _SERVICE_TOKENS[cache_key] = _CachedServiceToken(
            access_token=access_token,
            expires_at_monotonic=time.monotonic() + max(float(expires_in_seconds), 60.0),
        )
        return f"Bearer {access_token}"


def build_auth_startup_checks(settings: ServiceSettings) -> list[ProbeCallable]:
    """Build startup probes for JWT validation infrastructure."""
    if settings.auth_mode == "disabled":
        return []

    async def config_probe() -> None:
        _validation_config(settings)

    async def provider_probe() -> None:
        jwks_url = _validation_config(settings)
        if jwks_url is not None:
            await _probe_endpoint(
                jwks_url,
                operation="auth_jwks_probe",
                timeout_seconds=settings.startup_probe_timeout_seconds,
            )
        token_url = _normalized(settings.auth_service_token_url)
        if token_url is not None:
            await _probe_endpoint(
                token_url,
                operation="auth_service_token_probe",
                timeout_seconds=settings.startup_probe_timeout_seconds,
            )

    return [config_probe, provider_probe]


async def _decode_token(
    *,
    settings: ServiceSettings,
    token: str,
    operation: str,
) -> ClaimSet:
    try:
        shared_secret = _normalized(settings.auth_shared_secret)
        if shared_secret is not None:
            decoded = jwt.decode(
                token,
                shared_secret,
                algorithms=["HS256"],
                issuer=_normalized(settings.auth_issuer) or None,
                options={"verify_aud": False},
                leeway=settings.auth_clock_skew_seconds,
            )
            return _ensure_mapping(decoded, operation=operation)

        jwks_url = _validation_config(settings)
        if jwks_url is None:
            raise ServiceError(
                error_code="AUTH_CONFIG_INVALID",
                message="JWKS URL or shared secret must be configured when auth is enabled",
                operation=operation,
                status_code=500,
            )
        jwks_client = _JWKS_CLIENTS.get(jwks_url)
        if jwks_client is None:
            jwks_client = PyJWKClient(jwks_url)
            _JWKS_CLIENTS[jwks_url] = jwks_client
        signing_key = await asyncio.to_thread(jwks_client.get_signing_key_from_jwt, token)
        decoded = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            issuer=_normalized(settings.auth_issuer) or None,
            options={"verify_aud": False},
            leeway=settings.auth_clock_skew_seconds,
        )
        return _ensure_mapping(decoded, operation=operation)
    except ServiceError:
        raise
    except PyJWKClientConnectionError as exc:
        raise ServiceError(
            error_code="AUTH_PROVIDER_UNAVAILABLE",
            message="JWT signing key provider is unavailable",
            operation=operation,
            status_code=503,
            cause=str(exc),
        ) from exc
    except InvalidTokenError as exc:
        raise ServiceError(
            error_code="AUTH_INVALID_TOKEN",
            message="Bearer token failed JWT validation",
            operation=operation,
            status_code=401,
            cause=str(exc),
        ) from exc


def _build_principal(*, claims: ClaimSet, operation: str) -> AuthenticatedPrincipal:
    subject = claims.get("sub")
    if not isinstance(subject, str) or subject.strip() == "":
        raise ServiceError(
            error_code="AUTH_PROVIDER_RESPONSE_INVALID",
            message="JWT claims do not contain a valid subject",
            operation=operation,
            status_code=502,
        )
    return AuthenticatedPrincipal(
        subject=subject,
        scopes=frozenset(_extract_scopes(claims)),
        audiences=frozenset(_extract_audiences(claims, operation=operation)),
        claims=claims,
    )


def _enforce_scope(
    principal: AuthenticatedPrincipal,
    *,
    settings: ServiceSettings,
    operation: str,
) -> None:
    required_scope = _normalized(settings.auth_required_scope)
    if required_scope is None:
        return
    if required_scope not in principal.scopes:
        raise ServiceError(
            error_code="AUTH_FORBIDDEN_SCOPE",
            message="Token does not contain required scope",
            operation=operation,
            status_code=403,
            cause=required_scope,
        )


def _enforce_audience(
    principal: AuthenticatedPrincipal,
    *,
    settings: ServiceSettings,
    operation: str,
) -> None:
    required_audience = _normalized(settings.auth_required_audience)
    if required_audience is None:
        return
    if required_audience not in principal.audiences:
        raise ServiceError(
            error_code="AUTH_FORBIDDEN_AUDIENCE",
            message="Token does not contain required audience",
            operation=operation,
            status_code=403,
            cause=required_audience,
        )


def _extract_scopes(claims: ClaimSet) -> set[str]:
    scopes: set[str] = set()
    for key in ("scope", "scp"):
        raw_scope = claims.get(key)
        if isinstance(raw_scope, str):
            scopes.update(item for item in raw_scope.split() if item.strip() != "")
        else:
            scope_items = _as_object_sequence(raw_scope)
            if scope_items is None:
                continue
            for item in scope_items:
                if isinstance(item, str) and item.strip() != "":
                    scopes.add(item)
    return scopes


def _extract_audiences(claims: ClaimSet, *, operation: str) -> set[str]:
    raw_audience = claims.get("aud")
    if raw_audience is None:
        return set()
    if isinstance(raw_audience, str):
        return {raw_audience}
    audience_items = _as_object_sequence(raw_audience)
    if audience_items is not None:
        audiences: set[str] = set()
        for item in audience_items:
            if not isinstance(item, str):
                raise ServiceError(
                    error_code="AUTH_PROVIDER_RESPONSE_INVALID",
                    message="JWT audience claim contains non-string value",
                    operation=operation,
                    status_code=502,
                    cause=str(list(audience_items)),
                )
            audiences.add(item)
        return audiences
    raise ServiceError(
        error_code="AUTH_PROVIDER_RESPONSE_INVALID",
        message="JWT audience claim is malformed",
        operation=operation,
        status_code=502,
        cause=type(raw_audience).__name__,
    )


def _validation_config(settings: ServiceSettings) -> str | None:
    shared_secret = _normalized(settings.auth_shared_secret)
    if shared_secret is not None:
        return None
    jwks_url = _normalized(settings.auth_jwks_url)
    if jwks_url is not None:
        return jwks_url
    raise ServiceError(
        error_code="AUTH_CONFIG_INVALID",
        message="AUTH_JWKS_URL or AUTH_SHARED_SECRET is required when auth is enabled",
        operation="auth_config",
        status_code=500,
    )


async def _request_service_token(
    *,
    token_url: str,
    client_id: str,
    client_secret: str,
    scope: str | None,
    audience: str | None,
    timeout_seconds: float,
) -> tuple[str, int]:
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }
    if scope is not None:
        data["scope"] = scope
    if audience is not None:
        data["audience"] = audience

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(
                token_url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
    except httpx.HTTPError as exc:
        raise ServiceError(
            error_code="AUTH_PROVIDER_UNAVAILABLE",
            message="Failed to acquire service token from authorization server",
            operation="service_auth_token_request",
            status_code=503,
            cause=str(exc),
        ) from exc

    if response.status_code >= 500:
        raise ServiceError(
            error_code="AUTH_PROVIDER_UNAVAILABLE",
            message="Authorization server is unavailable for service token requests",
            operation="service_auth_token_request",
            status_code=503,
            cause=f"http_status={response.status_code}",
        )
    if response.status_code >= 400:
        raise ServiceError(
            error_code="AUTH_CONFIG_INVALID",
            message="Authorization server rejected service token request",
            operation="service_auth_token_request",
            status_code=500,
            cause=f"http_status={response.status_code}",
        )

    payload = _ensure_mapping(response.json(), operation="service_auth_token_request")
    access_token = payload.get("access_token")
    expires_in = payload.get("expires_in")
    if not isinstance(access_token, str) or access_token.strip() == "":
        raise ServiceError(
            error_code="AUTH_PROVIDER_RESPONSE_INVALID",
            message="Authorization server response is missing access_token",
            operation="service_auth_token_request",
            status_code=502,
        )
    if not isinstance(expires_in, int | float):
        raise ServiceError(
            error_code="AUTH_PROVIDER_RESPONSE_INVALID",
            message="Authorization server response is missing expires_in",
            operation="service_auth_token_request",
            status_code=502,
        )
    return access_token, int(expires_in)


def _ensure_mapping(payload: object, *, operation: str) -> ClaimSet:
    if not isinstance(payload, Mapping):
        raise ServiceError(
            error_code="AUTH_PROVIDER_RESPONSE_INVALID",
            message="Authorization provider returned malformed JSON payload",
            operation=operation,
            status_code=502,
        )
    return cast(ClaimSet, payload)


def _required_setting(
    value: str | None,
    *,
    error_code: str,
    message: str,
    operation: str,
) -> str:
    normalized = _normalized(value)
    if normalized is None:
        raise ServiceError(
            error_code=error_code,
            message=message,
            operation=operation,
            status_code=500,
        )
    return normalized


def _normalized(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if stripped == "":
        return None
    return stripped


def _as_object_sequence(value: object) -> Sequence[object] | None:
    if isinstance(value, list):
        return cast(list[object], value)
    return None


async def _probe_endpoint(url: str, *, operation: str, timeout_seconds: float) -> None:
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port or _default_port(parsed.scheme)
    if host is None or port is None:
        raise ServiceError(
            error_code="AUTH_PROVIDER_INVALID_URL",
            message="Authorization endpoint URL is missing host or port",
            operation=operation,
            status_code=500,
            cause=url,
        )
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host=host, port=port),
            timeout=timeout_seconds,
        )
        writer.close()
        await writer.wait_closed()
    except Exception as exc:
        raise ServiceError(
            error_code="AUTH_PROVIDER_UNREACHABLE",
            message="Authorization endpoint is not reachable during startup checks",
            operation=operation,
            status_code=503,
            cause=f"{host}:{port} -> {exc}",
        ) from exc


def _default_port(scheme: str) -> int | None:
    return {
        "http": 80,
        "https": 443,
    }.get(scheme.lower())
