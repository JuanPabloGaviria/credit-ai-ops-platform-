"""Prometheus metrics wrappers with bounded label cardinality."""

from __future__ import annotations

import re

from prometheus_client import Counter, Histogram

_REQUEST_COUNTER = Counter(
    "http_requests_total",
    "Total HTTP requests",
    labelnames=("service", "route", "method", "status_code"),
    namespace="credit_ai_ops",
)
_REQUEST_LATENCY = Histogram(
    "http_request_latency_seconds",
    "HTTP request latency in seconds",
    labelnames=("service", "route", "method"),
    namespace="credit_ai_ops",
)
_INTEGRATION_COUNTER = Counter(
    "integration_calls_total",
    "Total outbound integration calls",
    labelnames=("service", "dependency", "operation", "outcome"),
    namespace="credit_ai_ops",
)
_INTEGRATION_LATENCY = Histogram(
    "integration_call_latency_seconds",
    "Outbound integration call latency in seconds",
    labelnames=("service", "dependency", "operation", "outcome"),
    namespace="credit_ai_ops",
)
_NON_ALNUM_PATTERN = re.compile(r"[^a-z0-9]+")


class MetricsRegistry:
    """Service-local metrics facade over shared collectors."""

    def __init__(self, service_name: str) -> None:
        self._service_name = self._label_safe(service_name)

    def observe_request(
        self,
        route: str,
        method: str,
        status_code: int,
        latency_seconds: float,
    ) -> None:
        safe_route = self._route_safe(route)
        _REQUEST_COUNTER.labels(
            service=self._service_name,
            route=safe_route,
            method=method,
            status_code=str(status_code),
        ).inc()
        _REQUEST_LATENCY.labels(
            service=self._service_name,
            route=safe_route,
            method=method,
        ).observe(latency_seconds)

    def observe_integration_call(
        self,
        *,
        dependency: str,
        operation: str,
        outcome: str,
        latency_seconds: float,
    ) -> None:
        safe_dependency = self._label_safe(dependency)
        safe_operation = self._label_safe(operation)
        safe_outcome = self._label_safe(outcome)
        _INTEGRATION_COUNTER.labels(
            service=self._service_name,
            dependency=safe_dependency,
            operation=safe_operation,
            outcome=safe_outcome,
        ).inc()
        _INTEGRATION_LATENCY.labels(
            service=self._service_name,
            dependency=safe_dependency,
            operation=safe_operation,
            outcome=safe_outcome,
        ).observe(latency_seconds)

    @classmethod
    def _route_safe(cls, value: str) -> str:
        normalized = re.sub(r"\{[^}]+\}", "{param}", value)
        return cls._label_safe(normalized)

    @staticmethod
    def _label_safe(value: str) -> str:
        lowered = value.strip().lower()
        normalized = _NON_ALNUM_PATTERN.sub("_", lowered).strip("_")
        return normalized or "root"
