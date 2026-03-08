# Executive Brief

**Language:** [Versión en Español](brief.md) · English (primary)

## Objective
Validate end-to-end AI delivery capability for regulated banking: from use-case definition to production operation with model governance, security controls, and measurable reliability.

## Value Thesis
The platform demonstrates three banking-critical capabilities:

1. Delivery of an AI product with operational impact.
2. Engineering controls that reduce operational and model risk.
3. Clear mapping from business outcomes to technical evidence.

## Expected Business Outcomes
- reduced credit evaluation friction
- improved decision turnaround time
- stronger audit and compliance traceability
- lower incident impact through explicit recovery paths (DLQ/replay)

## High-Level Technical Evidence
- **Complete async chain:** `application -> feature -> scoring -> decision -> assistant -> audit`
- **SQL persistence plus typed contracts:** versioned repositories and schemas
- **Reproducible MLOps:** train/evaluate/register/promote plus model card generation
- **Resilience controls:** timeout, retry, circuit-breaker, bulkhead, idempotency
- **Security and supply chain controls:** SAST, dependency audit, secret scan, SBOM, image signing

## Metrics and SLOs
- gateway p95 `<= 300ms`
- async p95 `<= 2s`
- 5xx rate `< 1%` over rolling 15-minute windows

These are operating targets. Static latency snapshots are not published in markdown because
they stop being trustworthy once the environment, network path, or deployed topology changes.
Current evidence should be regenerated before quoting external performance numbers.

## Reviewer Checklist
1. Run `make recruiter-demo` and inspect `build/recruiter-demo-report.md`.
2. Verify MLOps evidence in `build/recruiter-ml-evidence.json`.
3. Review `build/reviewer-scorecard.md` for control-to-evidence mapping.
4. Run `make bank-cybersec-gate` for security posture.
5. Run `pytest -m integration -vv` for real operational reliability.
6. Confirm traceability via `GET /v1/audit/traces/{trace_id}`.

## Key Architectural Decisions
- domain microservices in a monorepo with unified quality gates
- contract-first REST and event evolution
- async integration with outbox/inbox consistency patterns
- deterministic fallback mode for internal assistant behavior
