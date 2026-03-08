# ADR 0003: Retry and Idempotency Strategy

## Status
Accepted

## Decision
Apply bounded retries with jitter, circuit-breaker guards, and persistent idempotency-key semantics for write operations.

Gateway idempotency policy:
- Require `x-idempotency-key` for credit evaluation writes.
- Persist `idempotency_key`, `endpoint`, `request_hash`, and `response_payload` in Postgres.
- Replay the stored response for exact retries.
- Return `409` for payload mismatch or when a same-key request is already in progress.

## Rationale
Improves recoverability under transient failures while preventing duplicate side effects.
