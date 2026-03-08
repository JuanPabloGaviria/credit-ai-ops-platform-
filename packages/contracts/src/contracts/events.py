"""Typed event envelope schema."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EventEnvelope(BaseModel):
    """Canonical event envelope for broker transport."""

    model_config = ConfigDict(extra="forbid")

    event_name: str = Field(pattern=r"^[a-z]+\.[a-z_]+\.[a-z_]+\.v\d+$")
    event_id: str = Field(min_length=8)
    trace_id: str = Field(min_length=8)
    correlation_id: str | None = Field(default=None, min_length=8)
    causation_id: str | None = Field(default=None, min_length=8)
    producer: str = Field(min_length=1)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any]

    @model_validator(mode="after")
    def _default_correlation_id(self) -> EventEnvelope:
        if self.correlation_id is None:
            self.correlation_id = self.trace_id
        if self.causation_id is not None and self.causation_id.strip() == "":
            self.causation_id = None
        return self
