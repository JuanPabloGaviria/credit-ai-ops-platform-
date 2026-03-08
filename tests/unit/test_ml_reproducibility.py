import pytest

from shared_kernel import build_model_metadata


@pytest.mark.unit
def test_model_metadata_contains_required_repro_fields() -> None:
    metadata = build_model_metadata(
        model_name="credit-risk",
        model_version="v1.0.0",
        dataset_hash="0123456789abcdef",
        random_seed=42,
        environment_fingerprint="python3.11-uv",
    )

    assert metadata.dataset_hash == "0123456789abcdef"
    assert metadata.random_seed == 42
    assert metadata.environment_fingerprint == "python3.11-uv"
