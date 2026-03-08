# MLOps Lifecycle Runbook

## Scope
This runbook covers deterministic model lifecycle endpoints in the `mlops` service:
- `POST /v1/mlops/train`
- `POST /v1/mlops/evaluate`
- `POST /v1/mlops/register`
- `POST /v1/mlops/promote`
- `GET /v1/mlops/runs/{run_id}`

## Preconditions
- `POSTGRES_DSN` and `RABBITMQ_URL` are reachable.
- SQL migrations are applied, including `services/mlops/migrations/0002_lifecycle.sql`.
- Client passes `x-idempotency-key` header for all write operations.

## Operational Flow
1. `train`: generates deterministic metrics and model artifact from `dataset_reference + random_seed`, then persists run metadata.
2. `evaluate`: loads training run, applies policy thresholds (`MLOPS_MIN_AUC`, `MLOPS_MAX_CALIBRATION_ERROR`), and persists evaluation.
3. `register`: validates run/evaluation linkage, enforces policy pass, writes model card, and stores candidate in registry.
4. `promote`: marks candidate as promoted and enqueues `credit.model.promoted.v1` via outbox for reliable publication.

## Failure Signals
- `ML_RUN_NOT_FOUND`: unknown training run id.
- `ML_EVALUATION_NOT_FOUND`: unknown evaluation id.
- `ML_RUN_EVALUATION_MISMATCH`: mismatched run/evaluation pair.
- `ML_POLICY_GATE_FAILED`: evaluation exists but did not meet policy thresholds.
- `MODEL_VERSION_NOT_FOUND`: attempted promotion of missing candidate.

## Recovery
- If promotion publish fails, re-run outbox relay path by calling `promote` again with the same idempotency key and tracing logs by `trace_id`.
- For artifact/model-card path issues, validate the configured backend:
  - `filesystem`: check `ARTIFACT_ROOT_DIR` permissions and available disk space.
  - `azure_blob`: check `ARTIFACT_BLOB_ACCOUNT_URL`, `ARTIFACT_BLOB_CONTAINER_NAME`, managed identity assignment, and `Storage Blob Data Contributor` access on the artifact account.
