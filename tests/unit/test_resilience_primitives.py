import asyncio

import pytest

from shared_kernel import ServiceError
from shared_kernel.resilience import Bulkhead, CircuitBreaker, with_retries


@pytest.mark.unit
def test_circuit_breaker_opens_after_threshold() -> None:
    breaker = CircuitBreaker(failure_threshold=2, success_threshold=1)
    breaker.record_failure()
    breaker.record_failure()

    assert breaker.is_open
    with pytest.raises(ServiceError) as error:
        breaker.assert_available("test_operation")
    assert error.value.error_code == "CIRCUIT_OPEN"


@pytest.mark.unit
def test_bulkhead_rejects_when_capacity_exceeded() -> None:
    async def scenario() -> None:
        bulkhead = Bulkhead(max_concurrency=1)
        entered = asyncio.Event()
        release = asyncio.Event()

        async def hold_slot() -> str:
            entered.set()
            await release.wait()
            return "ok"

        primary = asyncio.create_task(bulkhead.run("hold_slot", hold_slot))
        await entered.wait()

        with pytest.raises(ServiceError) as error:
            await bulkhead.run("second_slot", lambda: asyncio.sleep(0))
        assert error.value.error_code == "BULKHEAD_REJECTED"

        release.set()
        _ = await primary

    asyncio.run(scenario())


@pytest.mark.unit
def test_with_retries_does_not_retry_non_retryable_service_error() -> None:
    async def scenario() -> None:
        attempts = 0

        async def fail_once() -> None:
            nonlocal attempts
            attempts += 1
            raise ServiceError(
                error_code="CIRCUIT_OPEN",
                message="Circuit already open",
                operation="retry_test",
                status_code=503,
            )

        with pytest.raises(ServiceError) as error:
            await with_retries(
                operation="retry_test",
                attemptable=fail_once,
                max_attempts=3,
                base_delay_seconds=0.01,
                max_delay_seconds=0.02,
                jitter_seconds=0.0,
            )
        assert error.value.error_code == "CIRCUIT_OPEN"
        assert attempts == 1

    asyncio.run(scenario())
