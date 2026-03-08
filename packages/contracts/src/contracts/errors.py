"""Error contract schema."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field


class ErrorEnvelope(BaseModel):
    """Canonical fail-loud error response."""

    model_config = ConfigDict(extra="forbid")

    error_code: str = Field(min_length=3)
    message: str = Field(min_length=1)
    service: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    trace_id: str = Field(min_length=8)
    cause: str | None = None
    hint: str | None = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
