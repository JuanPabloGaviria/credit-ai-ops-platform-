# Reliability NFR Contract

System objective: **fault-tolerant, observable, recoverable**.

## Mandatory Controls
- Global outbound timeout policy (`request_timeout_seconds`)
- Bounded retries with exponential backoff and jitter
- Circuit-breaker and bulkhead isolation behavior for unstable dependencies
- Idempotency key support for external write operations
- Dead-letter queue and replay workflow support
- Graceful degradation for non-critical dependency outages
- Startup dependency checks and readiness endpoint gating

## Error and Trace Contract
- Typed error envelope with `error_code`, `operation`, `trace_id`, `cause`, and `hint`
- Structured JSON logs only
- Trace ID propagation across request chain
