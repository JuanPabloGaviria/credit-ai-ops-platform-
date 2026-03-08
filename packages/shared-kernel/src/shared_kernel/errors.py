"""Typed domain/service exceptions and conversion helpers."""

from __future__ import annotations

from dataclasses import dataclass

from contracts import ErrorEnvelope


@dataclass(slots=True)
class ServiceError(Exception):
    """Runtime error with explicit context for fail-loud semantics."""

    error_code: str
    message: str
    operation: str
    status_code: int = 500
    cause: str | None = None
    hint: str | None = None

    def to_envelope(self, service: str, trace_id: str) -> ErrorEnvelope:
        return ErrorEnvelope(
            error_code=self.error_code,
            message=self.message,
            service=service,
            operation=self.operation,
            trace_id=trace_id,
            cause=self.cause,
            hint=self.hint,
        )
