# Role-Alignment Matrix

Back to the flagship documents:

- [Main README](../../README.md)
- [Executive brief](brief.en.md)
- [Spanish role matrix](role-alignment.md)

This matrix is intentionally evidence-first. It maps the public surface of the repository to the capabilities a serious reviewer would probe.

| Capability under review | Evidence in the repository |
| --- | --- |
| End-to-end AI system ownership | gateway path plus async credit chain across `api-gateway`, `application`, `feature`, `scoring`, `decision`, `assistant`, and `audit` |
| Advanced Python service engineering | typed FastAPI services, versioned SQL migrations, validation layers, and contract publication under `services/*` and `schemas/*` |
| Durable asynchronous processing | outbox relay, retry, DLQ, replay, and idempotency exercised in `tests/integration/test_async_credit_chain.py` |
| MLOps lifecycle control | `train`, `evaluate`, `register`, and `promote` endpoints plus evidence artifacts in `build/recruiter-ml-evidence.json` |
| Security and platform judgment | secret scan, dependency posture, SBOM, container policy, and bank-grade gate via `make bank-cybersec-gate` |
| Operability under scrutiny | audit endpoints, trace-linked evidence, structured error posture, and recruiter-demo receipts |
| Communication with technical and non-technical audiences | `README.md`, `docs/executive/brief.en.md`, runbooks, ADRs, and reviewer scorecards |

## Highest-Signal Commands

```bash
make recruiter-demo
make release-ready
make bank-cybersec-gate
```

## Reading Order

1. [README.md](../../README.md)
2. [docs/executive/brief.en.md](brief.en.md)
3. [docs/runbooks/async-flow.md](../runbooks/async-flow.md)
4. [docs/runbooks/mlops-lifecycle.md](../runbooks/mlops-lifecycle.md)
5. `tests/e2e/test_gateway_http_stack.py`
6. `tests/integration/test_async_credit_chain.py`

## What This Matrix Does Not Do

It does not inflate the repo into a generic "full bank platform" claim.

It supports a narrower conclusion: the repository demonstrates real judgment in AI-heavy backend engineering where model lifecycle, auditability, and failure recovery all matter at the same time.
