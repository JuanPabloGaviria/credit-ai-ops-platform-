# Role Alignment Matrix (Amrop - AI Specialist)

**Language:** [Versión en Español](role-alignment.md) · English (primary)

This matrix maps role requirements to verifiable evidence in code, tests, and runbooks.

| Role requirement | Verifiable evidence |
|---|---|
| End-to-end AI/ML design and deployment | Full async chain in `docs/runbooks/async-flow.md` validated by `tests/integration/test_async_credit_chain.py` |
| Advanced Python and SQL engineering | Typed service implementations in `services/*/src/*`; versioned SQL migrations in `services/*/migrations/*.sql`; strict validation via `mypy`, `pyright`, `pydantic` |
| Robust and maintainable pipelines | Domain persistence with outbox/inbox; controlled DLQ replay in `scripts/dev/replay_dlq.py`; deterministic quality gates in `Makefile` |
| MLOps and model lifecycle ownership | `train/evaluate/register/promote` endpoints in `services/mlops`; runbook in `docs/runbooks/mlops-lifecycle.md`; lifecycle tests in `tests/unit/test_mlops_lifecycle.py` |
| API-first model deployment (FastAPI) | FastAPI applications in `services/*/src/*/main.py`; versioned API contracts in `schemas/openapi` |
| Cloud readiness (Azure) | Container Apps Terraform baseline in `infra/terraform/container-apps`; AKS next-stage documented in ADRs and infra docs |
| Communication with non-technical leadership | Executive narrative in `docs/executive/brief.md`; reproducible business+technical walkthrough via `make recruiter-demo` |
| Innovation under risk control | Progressive gates `pre-commit-gate`, `cybersec-posture`, `bank-cybersec-gate`; architecture decisions captured in `docs/adr/*` |

## High-Signal Commands
```bash
make recruiter-demo
make pre-commit-gate
make bank-cybersec-gate
```

## Expected Review Outcome
- confirmed end-to-end functional impact
- confirmed reliability under fault scenarios
- confirmed security posture and regulatory traceability
