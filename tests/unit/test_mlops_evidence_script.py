"""Unit tests for deterministic recruiter MLOps evidence generation."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.dev.generate_mlops_evidence import DEFAULT_MODEL_NAME, generate_evidence


def test_generate_evidence_persists_expected_artifacts(tmp_path: Path) -> None:
    output_path = tmp_path / "recruiter-ml-evidence.json"
    artifact_root = tmp_path / "mlops-artifacts"

    payload = generate_evidence(output_path=output_path, artifact_root=artifact_root)

    assert output_path.exists(), "evidence report file must be created"
    loaded = json.loads(output_path.read_text(encoding="utf-8"))
    assert loaded["model_name"] == DEFAULT_MODEL_NAME
    assert loaded["determinism_check"]["digest_stable_across_runs"] is True
    assert loaded["policy_result"]["passed"] is True

    artifact_uri = Path(loaded["training_run"]["artifact_uri"])
    assert artifact_uri.exists(), "training artifact must be written"
    model_card_uri = Path(loaded["model_card"]["uri"])
    assert model_card_uri.exists(), "model card artifact must be written"

    assert payload["training_run"]["artifact_digest"] == loaded["training_run"]["artifact_digest"]
