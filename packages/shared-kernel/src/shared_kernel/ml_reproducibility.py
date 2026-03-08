"""Reproducibility metadata helpers for MLOps artifacts."""

from __future__ import annotations

from contracts import ModelMetadata


def build_model_metadata(
    *,
    model_name: str,
    model_version: str,
    dataset_hash: str,
    random_seed: int,
    environment_fingerprint: str,
) -> ModelMetadata:
    """Build reproducibility metadata with fixed-seed requirements."""
    return ModelMetadata(
        model_name=model_name,
        model_version=model_version,
        dataset_hash=dataset_hash,
        random_seed=random_seed,
        environment_fingerprint=environment_fingerprint,
    )
