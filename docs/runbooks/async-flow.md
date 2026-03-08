# Async Flow Runbook

## Event Flow
1. `application` publishes `credit.application.submitted.v1`
2. `feature` consumes and publishes `credit.feature.materialized.v1`
3. `scoring` consumes and publishes `credit.scoring.generated.v1`
4. `decision` consumes and publishes `credit.decision.made.v1`
5. `collab-assistant` consumes decision events and publishes `credit.assistant.summarized.v1`
6. `observability-audit` consumes `credit.#`

## Queue Topology
- Exchange: `credit.events` (topic)
- DLX: `credit.events.dlx` (topic)
- Service queues:
  - `feature.application_submitted`
  - `scoring.feature_materialized`
  - `decision.scoring_generated`
  - `assistant.decision_made`
  - `audit.credit_events`
- Each queue has `<queue>.dlq` bound to DLX

## Worker Commands
```bash
source .venv/bin/activate
export POSTGRES_DSN=postgresql://credit:credit@localhost:5432/credit_ai_ops
export RABBITMQ_URL=amqp://guest:guest@localhost:5672/
export APP_VERSION=0.1.0
export REQUEST_TIMEOUT_SECONDS=3.0
export STARTUP_PROBE_TIMEOUT_SECONDS=2.0
export RETRY_MAX_ATTEMPTS=3
export RETRY_BASE_DELAY_SECONDS=0.1
export RETRY_MAX_DELAY_SECONDS=5.0
export RETRY_JITTER_SECONDS=0.2
export BROKER_CIRCUIT_FAILURE_THRESHOLD=5
export BROKER_CIRCUIT_SUCCESS_THRESHOLD=2
export BROKER_BULKHEAD_MAX_CONCURRENCY=10
export BROKER_PREFETCH_COUNT=10
python scripts/dev/apply_migrations.py --postgres-dsn "$POSTGRES_DSN"
export PYTHONPATH="packages/shared-kernel/src:packages/contracts/src:packages/observability/src:packages/security/src:services/feature/src:services/scoring/src:services/decision/src:services/collab-assistant/src:services/observability-audit/src"
python services/feature/src/feature_service/worker.py
python services/scoring/src/scoring_service/worker.py
python services/decision/src/decision_service/worker.py
python services/collab-assistant/src/collab_assistant/worker.py
python services/observability-audit/src/observability_audit/worker.py
```

## Outbox Relay Commands
```bash
source .venv/bin/activate
export SERVICE=application
make relay

# one-shot cycle
python scripts/dev/run_outbox_relay.py --service application --once
```

Supported relay services:
- `application`
- `feature`
- `scoring`
- `decision`
- `collab-assistant`
- `mlops`

## DLQ Replay
```bash
source .venv/bin/activate
export RABBITMQ_URL=amqp://guest:guest@localhost:5672/
python scripts/dev/replay_dlq.py feature.application_submitted \
  --rabbitmq-url "$RABBITMQ_URL" \
  --limit 100
```

## Integration Test
```bash
docker compose up -d postgres rabbitmq
source .venv/bin/activate
pytest -m integration -vv
```

The integration test fails fast on timeout and skips with an explicit reason if Postgres or RabbitMQ
is unreachable.

## Perf Baseline Checks
```bash
source .venv/bin/activate
pytest -m perf -vv
```

Perf coverage:
- async processing delay check (`application -> decision`) against the 2s baseline

Local in-process gateway smoke checks are intentionally excluded from `perf` because they do not
exercise the deployed network path and are not suitable as reviewer-facing latency evidence.
