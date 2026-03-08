import pytest

from shared_kernel.errors import ServiceError


@pytest.mark.unit
def test_service_error_maps_to_contract() -> None:
    error = ServiceError(
        error_code="INVALID_INPUT",
        message="Input failed validation",
        operation="score_application",
        status_code=400,
        hint="Verify required applicant features are present",
    )

    envelope = error.to_envelope(service="scoring", trace_id="trace-12345678")

    assert envelope.error_code == "INVALID_INPUT"
    assert envelope.service == "scoring"
    assert envelope.trace_id == "trace-12345678"
