#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPORT_PATH="$ROOT_DIR/build/recruiter-demo-report.md"
CURRENT_STEP="bootstrap"
START_TS="$(date +%s)"
CURRENT_GIT_COMMIT=""
CURRENT_GIT_BRANCH=""
CURRENT_CI_RUN_ID="${GITHUB_RUN_ID:-}"

trap 'fail "unexpected error during ${CURRENT_STEP}"' ERR

log() {
  printf '[recruiter-demo] %s\n' "$1"
}

run_checked() {
  local failure_message="$1"
  shift
  if ! "$@"; then
    fail "$failure_message"
  fi
}

fail() {
  local message="$1"
  trap - ERR
  local end_ts
  end_ts="$(date +%s)"
  local elapsed="$((end_ts - START_TS))"

  mkdir -p "$ROOT_DIR/build"
  cat >"$REPORT_PATH" <<EOF
# Recruiter Demo Report

- Status: FAILED
- Failed step: ${CURRENT_STEP}
- Duration: ${elapsed}s
- Timestamp (UTC): $(date -u +"%Y-%m-%dT%H:%M:%SZ")

## Failure Detail
${message}

## Recovery
Run the command again after fixing the failing step:

\`\`\`bash
make recruiter-demo
\`\`\`
EOF

  printf '[recruiter-demo] FAIL %s\n' "$message" >&2
  exit 1
}

require_command() {
  local command_name="$1"
  if ! command -v "$command_name" >/dev/null 2>&1; then
    fail "required command not found: ${command_name}"
  fi
}

wait_for_tcp() {
  local host="$1"
  local port="$2"
  local timeout_seconds="$3"

  python - <<'PY' "$host" "$port" "$timeout_seconds"
import socket
import sys
import time

host = sys.argv[1]
port = int(sys.argv[2])
timeout_seconds = float(sys.argv[3])
deadline = time.monotonic() + timeout_seconds

while time.monotonic() < deadline:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        if sock.connect_ex((host, port)) == 0:
            raise SystemExit(0)
    time.sleep(0.2)

raise SystemExit(1)
PY
}

write_validation_receipt() {
  local label="$1"
  local command_text="$2"
  local receipt_path="$ROOT_DIR/build/reviewer-validations/${label}.json"

  mkdir -p "$ROOT_DIR/build/reviewer-validations"
  python - <<'PY' \
    "$receipt_path" \
    "$command_text" \
    "$CURRENT_GIT_COMMIT" \
    "$CURRENT_GIT_BRANCH" \
    "$CURRENT_CI_RUN_ID"
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

receipt_path = Path(sys.argv[1])
command_text = sys.argv[2]
git_commit = sys.argv[3]
git_branch = sys.argv[4]
ci_run_id = sys.argv[5] or None
receipt_path.write_text(
    json.dumps(
        {
            "command": command_text,
            "status": "PASS",
            "generated_at_utc": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "git_commit": git_commit,
            "git_branch": git_branch,
            "ci_run_id": ci_run_id,
        },
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
PY
}

main() {
  cd "$ROOT_DIR"

  CURRENT_STEP="preflight"
  require_command docker
  require_command python3
  require_command git

  if [[ ! -f ".venv/bin/activate" ]]; then
    fail "virtual environment not found at .venv. Run ./scripts/dev/bootstrap.sh first."
  fi
  # shellcheck disable=SC1091
  source .venv/bin/activate

  local default_pythonpath
  default_pythonpath="packages/shared-kernel/src:packages/contracts/src:packages/observability/src:packages/security/src:services/api-gateway/src:services/application/src:services/feature/src:services/scoring/src:services/decision/src:services/collab-assistant/src:services/mlops/src:services/observability-audit/src"
  export PYTHONPATH="${PYTHONPATH:-$default_pythonpath}"
  CURRENT_GIT_COMMIT="$(git rev-parse HEAD)"
  CURRENT_GIT_BRANCH="$(git rev-parse --abbrev-ref HEAD)"

  export POSTGRES_DSN="${POSTGRES_DSN:-postgresql://credit:credit@localhost:5432/credit_ai_ops}"
  export RABBITMQ_URL="${RABBITMQ_URL:-amqp://guest:guest@localhost:5672/}"

  CURRENT_STEP="infrastructure_startup"
  log "starting postgres and rabbitmq"
  run_checked \
    "docker compose failed to start postgres and rabbitmq" \
    docker compose up -d postgres rabbitmq

  CURRENT_STEP="dependency_readiness"
  log "waiting for postgres and rabbitmq sockets"
  wait_for_tcp "127.0.0.1" "5432" "30" || fail "postgres did not become ready on 127.0.0.1:5432 within 30s"
  wait_for_tcp "127.0.0.1" "5672" "30" || fail "rabbitmq did not become ready on 127.0.0.1:5672 within 30s"

  CURRENT_STEP="database_migrations"
  log "applying service migrations"
  run_checked \
    "service migrations failed" \
    python scripts/dev/apply_migrations.py --postgres-dsn "$POSTGRES_DSN"
  write_validation_receipt "database_migrations" \
    "python scripts/dev/apply_migrations.py --postgres-dsn \$POSTGRES_DSN"

  CURRENT_STEP="mlops_evidence_generation"
  log "generating deterministic mlops evidence artifacts"
  run_checked \
    "deterministic mlops evidence generation failed" \
    python scripts/dev/generate_mlops_evidence.py

  CURRENT_STEP="cybersecurity_gate"
  log "running bank-grade cybersecurity gate"
  run_checked \
    "bank-grade cybersecurity gate failed" \
    make bank-cybersec-gate
  write_validation_receipt "cybersecurity_gate" "make bank-cybersec-gate"

  CURRENT_STEP="async_credit_chain_validation"
  log "running relay-only integration validation"
  run_checked \
    "relay-only integration validation failed" \
    pytest tests/integration/test_async_credit_chain.py -q
  write_validation_receipt "async_credit_chain_validation" \
    "pytest tests/integration/test_async_credit_chain.py -q"

  CURRENT_STEP="gateway_http_e2e_validation"
  log "running real HTTP gateway end-to-end validation"
  run_checked \
    "real HTTP gateway end-to-end validation failed" \
    pytest tests/e2e/test_gateway_http_stack.py -q
  write_validation_receipt "gateway_http_e2e_validation" \
    "pytest tests/e2e/test_gateway_http_stack.py -q"

  CURRENT_STEP="reviewer_scorecard_bundle"
  log "generating reviewer scorecard bundle"
  run_checked \
    "reviewer scorecard generation failed" \
    python scripts/ci/generate_reviewer_scorecard.py \
    --validation-receipt "database_migrations=build/reviewer-validations/database_migrations.json" \
    --validation-receipt "cybersecurity_gate=build/reviewer-validations/cybersecurity_gate.json" \
    --validation-receipt "async_credit_chain_validation=build/reviewer-validations/async_credit_chain_validation.json" \
    --validation-receipt "gateway_http_e2e_validation=build/reviewer-validations/gateway_http_e2e_validation.json" \
    --artifact "validation_receipts=build/reviewer-validations" \
    --artifact "mlops=build/recruiter-ml-evidence.json"

  CURRENT_STEP="report_generation"
  local end_ts
  end_ts="$(date +%s)"
  local elapsed="$((end_ts - START_TS))"
  mkdir -p "$ROOT_DIR/build"

  cat >"$REPORT_PATH" <<EOF
# Recruiter Demo Report

- Status: PASSED
- Duration: ${elapsed}s
- Timestamp (UTC): $(date -u +"%Y-%m-%dT%H:%M:%SZ")

## Validations Executed
1. Local infrastructure startup (Postgres + RabbitMQ)
2. Service migrations across all domains
3. Bank-grade cybersecurity gate (\`make bank-cybersec-gate\`)
4. Relay-only async credit chain validation (\`pytest tests/integration/test_async_credit_chain.py -q\`)
5. Real HTTP gateway end-to-end validation (\`pytest tests/e2e/test_gateway_http_stack.py -q\`)
6. Deterministic MLOps evidence generation (\`build/recruiter-ml-evidence.json\`)
7. Reviewer scorecard generation from validation receipts (\`build/reviewer-scorecard.md\`)

## Generated Artifacts
- \`build/recruiter-ml-evidence.json\`
- \`build/recruiter-mlops/artifacts/*.json\`
- \`build/recruiter-mlops/model_cards/*.json\`
- \`build/reviewer-validations/*.json\`
- \`build/reviewer-scorecard.md\`
- \`build/reviewer-scorecard.json\`

## Next Step
Share this report and the command used:

\`\`\`bash
make recruiter-demo
\`\`\`
EOF

  log "report written to $REPORT_PATH"
  log "recruiter demo passed"
}

main "$@"
