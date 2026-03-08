# ML Reproducibility Gate

Mandatory controls:
- Fixed random seeds for training/evaluation flows
- Dataset version hash attached to artifacts
- Environment fingerprint captured in model metadata
- Deterministic no-LLM collaborator mode always available

Reference implementation:
- `packages/shared-kernel/src/shared_kernel/ml_reproducibility.py`
- `services/mlops/src/mlops_service/lifecycle.py`
- `services/mlops/src/mlops_service/routes.py`
- `services/mlops/migrations/0002_lifecycle.sql`
