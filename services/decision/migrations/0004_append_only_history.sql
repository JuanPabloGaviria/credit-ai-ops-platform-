CREATE TABLE IF NOT EXISTS credit_decision_history (
  decision_id TEXT PRIMARY KEY,
  application_id TEXT NOT NULL,
  risk_score NUMERIC NOT NULL,
  decision TEXT NOT NULL,
  reason_codes TEXT[] NOT NULL,
  score_model_version TEXT NOT NULL,
  decision_source TEXT NOT NULL CHECK (decision_source IN ('manual_request', 'scoring_event', 'legacy_backfill')),
  trace_id TEXT NOT NULL,
  decision_event_id TEXT NOT NULL UNIQUE,
  decided_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_credit_decision_history_lookup
  ON credit_decision_history (application_id, decided_at DESC);

CREATE INDEX IF NOT EXISTS idx_credit_decision_history_trace
  ON credit_decision_history (trace_id, decided_at DESC);

INSERT INTO credit_decision_history (
  decision_id,
  application_id,
  risk_score,
  decision,
  reason_codes,
  score_model_version,
  decision_source,
  trace_id,
  decision_event_id,
  decided_at,
  created_at
)
SELECT
  CONCAT('legacy-decision-', md5(application_id || ':' || COALESCE(updated_at, created_at)::text)),
  application_id,
  risk_score,
  decision,
  reason_codes,
  'legacy_projection',
  'legacy_backfill',
  CONCAT('legacy-trace-', md5(application_id)),
  CONCAT('legacy-event-', md5(application_id || ':' || COALESCE(updated_at, created_at)::text)),
  COALESCE(updated_at, created_at),
  created_at
FROM credit_decisions
ON CONFLICT (decision_id) DO NOTHING;
