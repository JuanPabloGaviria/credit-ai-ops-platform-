from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import asyncpg
import pytest
from mlops_service.lifecycle import (
    build_signed_registered_model_artifact,
    train_deterministic_model,
)
from scoring_service.runtime import resolve_active_scoring_model

from contracts import FeatureVector, MLOpsRunResponse, RegisterModelRequest, TrainRunRequest
from shared_kernel import (
    DatabaseExecutor,
    ServiceError,
    ServiceSettings,
    build_filesystem_artifact_store,
)
from tests.defaults import (
    DEFAULT_POSTGRES_DSN,
    DEFAULT_RABBITMQ_URL,
    TEST_MODEL_SIGNING_KEY_ID,
    TEST_MODEL_SIGNING_PRIVATE_KEY_PEM,
    TEST_MODEL_SIGNING_PUBLIC_KEY_PEM,
)


class _FakeDatabase(DatabaseExecutor):
    def __init__(self, row: asyncpg.Record | None) -> None:
        self._row = row

    async def execute(self, query: str, *args: Any) -> str:
        _ = query
        _ = args
        return "SELECT 1"

    async def fetch(self, query: str, *args: Any) -> Sequence[asyncpg.Record]:
        _ = query
        _ = args
        return []

    async def fetchrow(self, query: str, *args: Any) -> asyncpg.Record | None:
        _ = query
        _ = args
        return self._row


def _settings(*, artifact_root: Path) -> ServiceSettings:
    return ServiceSettings(
        service_name="scoring",
        postgres_dsn=DEFAULT_POSTGRES_DSN,
        rabbitmq_url=DEFAULT_RABBITMQ_URL,
        artifact_root_dir=str(artifact_root),
        scoring_model_name="credit-risk",
        scoring_model_stage="production",
        model_signing_public_key_pem=TEST_MODEL_SIGNING_PUBLIC_KEY_PEM,
    )


def _signed_registry_row(
    *,
    tmp_path: Path,
    run: MLOpsRunResponse,
    model_version: str,
) -> dict[str, object]:
    artifact_store = build_filesystem_artifact_store(tmp_path)
    register_request = RegisterModelRequest(
        model_name=run.model_name,
        model_version=model_version,
        run_id=run.run_id,
        evaluation_id="eval-12345678",
        feature_spec_ref=run.feature_spec_ref,
        training_spec_ref=run.training_spec_ref,
    )
    signed_artifact = build_signed_registered_model_artifact(
        request=register_request,
        run=run,
        artifact_store=artifact_store,
        private_key_pem=TEST_MODEL_SIGNING_PRIVATE_KEY_PEM,
        signing_key_id=TEST_MODEL_SIGNING_KEY_ID,
    )
    return {
        "model_name": run.model_name,
        "model_version": model_version,
        "feature_spec_ref": run.feature_spec_ref,
        "training_spec_ref": run.training_spec_ref,
        "algorithm": run.algorithm,
        "artifact_uri": signed_artifact.artifact_uri,
        "artifact_digest": signed_artifact.artifact_digest,
        "signature_algorithm": signed_artifact.signature_algorithm,
        "signature_key_id": signed_artifact.signature_key_id,
        "artifact_signature": signed_artifact.artifact_signature,
    }


@pytest.mark.unit
def test_resolve_active_scoring_model_scores_with_promoted_artifact(tmp_path: Path) -> None:
    artifact_store = build_filesystem_artifact_store(tmp_path)
    run = train_deterministic_model(
        TrainRunRequest(
            model_name="credit-risk",
            dataset_reference="dataset://credit/runtime",
            random_seed=19,
        ),
        artifact_store=artifact_store,
    )
    row = cast(
        asyncpg.Record,
        _signed_registry_row(
            tmp_path=tmp_path,
            run=MLOpsRunResponse.model_validate(
                {
                    **run.model_dump(mode="json"),
                    "status": "succeeded",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "completed_at": "2026-01-01T00:00:00+00:00",
                }
            ),
            model_version="v1.2.3",
        ),
    )
    active_model = asyncio.run(
        resolve_active_scoring_model(
            db=_FakeDatabase(row),
            settings=_settings(artifact_root=tmp_path),
        )
    )

    prediction = active_model.score(
        FeatureVector(
            application_id="app-12345678",
            requested_amount=22000.0,
            debt_to_income=0.38,
            amount_to_income=0.37,
            credit_history_months=30,
            existing_defaults=0,
        )
    )

    assert prediction.model_version == "v1.2.3"
    assert 0 <= prediction.risk_score <= 1
    assert prediction.reason_codes


@pytest.mark.unit
def test_resolve_active_scoring_model_rejects_missing_promotion(tmp_path: Path) -> None:
    with pytest.raises(ServiceError) as error:
        asyncio.run(
            resolve_active_scoring_model(
                db=_FakeDatabase(None),
                settings=_settings(artifact_root=tmp_path),
            )
        )

    assert error.value.error_code == "SCORING_MODEL_NOT_PROMOTED"


@pytest.mark.unit
def test_resolve_active_scoring_model_rejects_digest_mismatch(tmp_path: Path) -> None:
    artifact_store = build_filesystem_artifact_store(tmp_path)
    run = train_deterministic_model(
        TrainRunRequest(
            model_name="credit-risk",
            dataset_reference="dataset://credit/runtime",
            random_seed=21,
        ),
        artifact_store=artifact_store,
    )
    row = cast(
        asyncpg.Record,
        {
            **_signed_registry_row(
                tmp_path=tmp_path,
                run=MLOpsRunResponse.model_validate(
                    {
                        **run.model_dump(mode="json"),
                        "status": "succeeded",
                        "created_at": "2026-01-01T00:00:00+00:00",
                        "completed_at": "2026-01-01T00:00:00+00:00",
                    }
                ),
                model_version="v1.2.4",
            ),
            "artifact_digest": "0" * 64,
        },
    )

    with pytest.raises(ServiceError) as error:
        asyncio.run(
            resolve_active_scoring_model(
                db=_FakeDatabase(row),
                settings=_settings(artifact_root=tmp_path),
            )
        )

    assert error.value.error_code == "SCORING_MODEL_ARTIFACT_DIGEST_MISMATCH"


@pytest.mark.unit
def test_resolve_active_scoring_model_rejects_artifact_path_escape(tmp_path: Path) -> None:
    outside_artifact = tmp_path.parent / "escaped-artifact.json"
    outside_artifact.write_text("{}", encoding="utf-8")
    row = cast(
        asyncpg.Record,
        {
            "model_name": "credit-risk",
            "model_version": "v1.2.5",
            "feature_spec_ref": "credit-feature-spec/v1",
            "training_spec_ref": "credit-training-spec/v1",
            "algorithm": "sklearn_logistic_regression",
            "artifact_uri": str(outside_artifact),
            "artifact_digest": "a" * 64,
            "signature_algorithm": "ed25519",
            "signature_key_id": TEST_MODEL_SIGNING_KEY_ID,
            "artifact_signature": "invalid",
        },
    )

    with pytest.raises(ServiceError) as error:
        asyncio.run(
            resolve_active_scoring_model(
                db=_FakeDatabase(row),
                settings=_settings(artifact_root=tmp_path),
            )
        )

    assert error.value.error_code == "SCORING_MODEL_ARTIFACT_PATH_INVALID"


@pytest.mark.unit
def test_resolve_active_scoring_model_rejects_invalid_signature(tmp_path: Path) -> None:
    artifact_store = build_filesystem_artifact_store(tmp_path)
    run = train_deterministic_model(
        TrainRunRequest(
            model_name="credit-risk",
            dataset_reference="dataset://credit/runtime",
            random_seed=23,
        ),
        artifact_store=artifact_store,
    )
    signed_row = dict(
        _signed_registry_row(
            tmp_path=tmp_path,
            run=MLOpsRunResponse.model_validate(
                {
                    **run.model_dump(mode="json"),
                    "status": "succeeded",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "completed_at": "2026-01-01T00:00:00+00:00",
                }
            ),
            model_version="v1.2.6",
        )
    )
    artifact_path = Path(cast(str, signed_row["artifact_uri"]))
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    payload["classifier"]["intercept"] = 99.0
    tampered_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    artifact_path.write_bytes(tampered_bytes)
    signed_row["artifact_digest"] = hashlib.sha256(tampered_bytes).hexdigest()

    with pytest.raises(ServiceError) as error:
        asyncio.run(
            resolve_active_scoring_model(
                db=_FakeDatabase(cast(asyncpg.Record, signed_row)),
                settings=_settings(artifact_root=tmp_path),
            )
        )

    assert error.value.error_code == "SCORING_MODEL_SIGNATURE_INVALID"
