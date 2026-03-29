# Executive Brief

Back to the flagship documents:

- [Main README](../../README.md)
- [Spanish executive brief](brief.md)
- [Role-alignment matrix](role-alignment.en.md)

## Executive Position

This repository presents credit decisioning as an operational system, not a model notebook with an API facade.

The business value is straightforward: faster and more explainable credit decisions with stronger control over operational risk, model risk, and audit reconstruction. The engineering value is that the same repo can answer three hard questions at once:

- what decision was made
- which model and policy produced it
- how confidently the system can recover when one of its dependencies fails

## What A Reviewer Should See

| Review question | Evidence path |
| --- | --- |
| Is this a real product flow instead of isolated ML work? | `POST /v1/gateway/credit-evaluate`, `tests/e2e/test_gateway_http_stack.py` |
| Can the decision path be reconstructed later? | audit endpoints plus trace-linked events |
| Is model promotion controlled? | `train -> evaluate -> register -> promote` lifecycle and signed artifacts |
| Does async failure have recovery semantics? | outbox relay, DLQ, replay, idempotency |
| Is the security posture executable? | `make bank-cybersec-gate` |

## Executive Summary Of The System

1. A credit application enters through the gateway.
2. Features are materialized and persisted with history.
3. Scoring resolves the promoted model package and produces a score.
4. Decision logic applies policy and returns approve, review, or reject with rationale.
5. Audit events preserve the operational path for review, debugging, and reconstruction.
6. The MLOps lifecycle governs how new models become eligible for scoring.

## Evidence That Matters Most

- `make recruiter-demo`
- `make release-ready`
- `tests/integration/test_async_credit_chain.py`
- `tests/e2e/test_gateway_http_stack.py`
- `build/recruiter-ml-evidence.json`
- `build/reviewer-scorecard.md`

## Business Reading

The repo is strongest when read as a control system around decision quality:

- the model is governed, not just served
- the decision is recorded, not just returned
- the failure path is designed, not improvised
- the review surface is reproducible, not manual

## Honest Boundaries

- No claim is made that this is a deployed bank production stack.
- No claim is made that markdown latency numbers are portable without fresh reruns.
- The claim that is made is narrower and stronger: the core credit path, its model governance loop, and its reviewer evidence are all concrete and reproducible.
