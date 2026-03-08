"""Shared idempotency header validation helpers."""

from __future__ import annotations

from .errors import ServiceError

IDEMPOTENCY_KEY_MIN_LENGTH = 8
DEFAULT_INVALID_IDEMPOTENCY_KEY_MESSAGE = (
    f"Idempotency key must be at least {IDEMPOTENCY_KEY_MIN_LENGTH} characters when provided"
)
DEFAULT_INVALID_IDEMPOTENCY_KEY_HINT = "Pass a UUID or equivalent stable request key"
DEFAULT_MISSING_IDEMPOTENCY_KEY_MESSAGE = "Idempotency key header is required for write operations"
DEFAULT_MISSING_IDEMPOTENCY_KEY_HINT = "Provide x-idempotency-key header with a stable request key"


def normalize_optional_idempotency_key(
    idempotency_key: str | None,
    *,
    operation: str,
    invalid_error_code: str = "INVALID_IDEMPOTENCY_KEY",
    invalid_message: str = DEFAULT_INVALID_IDEMPOTENCY_KEY_MESSAGE,
    invalid_hint: str = DEFAULT_INVALID_IDEMPOTENCY_KEY_HINT,
) -> str | None:
    """Validate and normalize optional idempotency key header."""
    if idempotency_key is None:
        return None
    normalized = idempotency_key.strip()
    if len(normalized) < IDEMPOTENCY_KEY_MIN_LENGTH:
        raise ServiceError(
            error_code=invalid_error_code,
            message=invalid_message,
            operation=operation,
            status_code=400,
            hint=invalid_hint,
        )
    return normalized


def require_idempotency_key(
    idempotency_key: str | None,
    *,
    operation: str,
    missing_error_code: str = "MISSING_IDEMPOTENCY_KEY",
    missing_message: str = DEFAULT_MISSING_IDEMPOTENCY_KEY_MESSAGE,
    missing_hint: str = DEFAULT_MISSING_IDEMPOTENCY_KEY_HINT,
    invalid_error_code: str = "INVALID_IDEMPOTENCY_KEY",
    invalid_message: str = DEFAULT_INVALID_IDEMPOTENCY_KEY_MESSAGE,
    invalid_hint: str = DEFAULT_INVALID_IDEMPOTENCY_KEY_HINT,
) -> str:
    """Require, validate, and normalize idempotency key header."""
    normalized = normalize_optional_idempotency_key(
        idempotency_key,
        operation=operation,
        invalid_error_code=invalid_error_code,
        invalid_message=invalid_message,
        invalid_hint=invalid_hint,
    )
    if normalized is not None:
        return normalized
    raise ServiceError(
        error_code=missing_error_code,
        message=missing_message,
        operation=operation,
        status_code=400,
        hint=missing_hint,
    )
