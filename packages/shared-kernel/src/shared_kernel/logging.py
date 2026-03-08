"""Structured JSON logging with PII redaction."""

from __future__ import annotations

import logging
from collections.abc import MutableMapping
from typing import Any

import structlog

from security import redact_pii

from .telemetry import current_span_identifiers


def _redact_processor(
    _: Any,
    __: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    return redact_pii(dict(event_dict))


def _otel_trace_processor(
    _: Any,
    __: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    trace_id, span_id = current_span_identifiers()
    if trace_id is not None:
        event_dict.setdefault("otel_trace_id", trace_id)
    if span_id is not None:
        event_dict.setdefault("otel_span_id", span_id)
    return event_dict


def configure_logging(log_level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(message)s",
    )
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.add_log_level,
            _otel_trace_processor,
            _redact_processor,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(service_name: str) -> structlog.BoundLogger:
    return structlog.get_logger(service=service_name)
