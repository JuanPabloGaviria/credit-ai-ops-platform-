# v1 SLO Baselines

- API gateway p95 latency <= 300ms
- Async event processing p95 delay <= 2s
- 5xx error budget < 1% per rolling 15-minute window

Current reviewer-facing automated evidence covers the async event-processing delay baseline in the
`perf` pipeline and the relay-backed async credit chain in the `integration` pipeline. Local
in-process gateway smoke checks are kept for fast regression detection but are not treated as
deployment-grade latency evidence.
