"""Resilience primitives: timeout, retry, and circuit breaker."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TypeVar

from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
    wait_random,
)

from .errors import ServiceError

T = TypeVar("T")
_NON_RETRYABLE_ERROR_CODES = frozenset({"CIRCUIT_OPEN", "BULKHEAD_REJECTED"})


@dataclass(slots=True)
class CircuitBreaker:
    """Small circuit breaker for outbound integration calls."""

    failure_threshold: int = 5
    success_threshold: int = 2
    recovery_timeout_seconds: float = 15.0
    _state: str = field(init=False, default="closed", repr=False)
    _failures: int = field(init=False, default=0, repr=False)
    _successes: int = field(init=False, default=0, repr=False)
    _opened_at_monotonic: float | None = field(init=False, default=None, repr=False)
    _half_open_trial_in_flight: bool = field(init=False, default=False, repr=False)
    _guard: threading.Lock = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if self.success_threshold < 1:
            raise ValueError("success_threshold must be >= 1")
        if self.recovery_timeout_seconds <= 0:
            raise ValueError("recovery_timeout_seconds must be > 0")
        self._guard = threading.Lock()

    @property
    def is_open(self) -> bool:
        return self._state == "open"

    def record_success(self) -> None:
        with self._guard:
            self._failures = 0
            if self._state == "half_open":
                self._successes += 1
                self._half_open_trial_in_flight = False
                if self._successes >= self.success_threshold:
                    self._state = "closed"
                    self._successes = 0
                    self._opened_at_monotonic = None
            else:
                self._successes = 0

    def record_failure(self) -> None:
        with self._guard:
            self._successes = 0
            self._half_open_trial_in_flight = False
            if self._state == "half_open":
                self._trip_locked()
                return
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._trip_locked()

    def assert_available(self, operation: str) -> None:
        with self._guard:
            if self._state == "open":
                if self._opened_at_monotonic is None:
                    self._trip_locked()
                elif (
                    time.monotonic() - self._opened_at_monotonic
                ) >= self.recovery_timeout_seconds:
                    self._state = "half_open"
                    self._successes = 0
                    self._half_open_trial_in_flight = False
                else:
                    raise ServiceError(
                        error_code="CIRCUIT_OPEN",
                        message="Circuit breaker is open for outbound dependency",
                        operation=operation,
                        status_code=503,
                        hint="Wait for automatic recovery timeout or inspect dependency health",
                    )
            if self._state == "half_open":
                if self._half_open_trial_in_flight:
                    raise ServiceError(
                        error_code="CIRCUIT_OPEN",
                        message="Circuit breaker is probing dependency recovery",
                        operation=operation,
                        status_code=503,
                        hint="Retry after the current half-open probe completes",
                    )
                self._half_open_trial_in_flight = True

    def _trip_locked(self) -> None:
        self._state = "open"
        self._opened_at_monotonic = time.monotonic()
        self._half_open_trial_in_flight = False


@dataclass(slots=True)
class Bulkhead:
    """Concurrency guard to isolate unstable outbound dependencies."""

    max_concurrency: int = 10
    _in_flight: int = field(init=False, default=0, repr=False)
    _guard: asyncio.Lock = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        self._guard = asyncio.Lock()

    async def run(self, operation: str, attemptable: Callable[[], Awaitable[T]]) -> T:
        async with self._guard:
            if self._in_flight >= self.max_concurrency:
                raise ServiceError(
                    error_code="BULKHEAD_REJECTED",
                    message="Bulkhead capacity exceeded for integration edge",
                    operation=operation,
                    status_code=503,
                    hint="Retry later or increase bulkhead concurrency budget",
                )
            self._in_flight += 1
        try:
            return await attemptable()
        finally:
            async with self._guard:
                self._in_flight -= 1


async def with_timeout(coro: Awaitable[T], timeout_seconds: float, operation: str) -> T:
    """Execute coroutine with hard timeout guard."""
    try:
        return await asyncio.wait_for(coro, timeout=timeout_seconds)
    except TimeoutError as exc:
        raise ServiceError(
            error_code="TIMEOUT",
            message="Outbound request timed out",
            operation=operation,
            status_code=504,
            cause=str(exc),
        ) from exc


def _is_retryable_exception(exc: BaseException) -> bool:
    if isinstance(exc, ServiceError):
        if exc.error_code in _NON_RETRYABLE_ERROR_CODES:
            return False
        return exc.status_code >= 500
    return True


async def with_retries(
    operation: str,
    attemptable: Callable[[], Awaitable[T]],
    max_attempts: int,
    base_delay_seconds: float,
    max_delay_seconds: float,
    jitter_seconds: float,
) -> T:
    """Execute async function with bounded retries and jitter."""
    async for attempt in AsyncRetrying(
        stop=stop_after_attempt(max_attempts),
        retry=retry_if_exception(_is_retryable_exception),
        wait=wait_exponential(
            multiplier=base_delay_seconds,
            min=base_delay_seconds,
            max=max_delay_seconds,
        )
        + wait_random(min=0, max=jitter_seconds),
        reraise=True,
    ):
        with attempt:
            return await attemptable()
    raise ServiceError(
        error_code="RETRY_ATTEMPTS_EXHAUSTED",
        message="Retry attempts were exhausted",
        operation=operation,
        status_code=503,
    )
