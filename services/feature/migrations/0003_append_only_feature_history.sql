CREATE TABLE IF NOT EXISTS feature_vector_history (
  materialization_id TEXT PRIMARY KEY,
  application_id TEXT NOT NULL,
  requested_amount NUMERIC NOT NULL,
  debt_to_income NUMERIC NOT NULL,
  amount_to_income NUMERIC NOT NULL,
  credit_history_months INTEGER NOT NULL,
  existing_defaults INTEGER NOT NULL,
  trace_id TEXT NOT NULL,
  feature_event_id TEXT NOT NULL UNIQUE,
  source_event_id TEXT,
  materialization_source TEXT NOT NULL
    CHECK (materialization_source IN ('manual_request', 'application_event', 'legacy_backfill')),
  materialized_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_feature_vector_history_application
  ON feature_vector_history (application_id, materialized_at DESC);

CREATE INDEX IF NOT EXISTS idx_feature_vector_history_trace
  ON feature_vector_history (trace_id, materialized_at DESC);

INSERT INTO feature_vector_history (
  materialization_id,
  application_id,
  requested_amount,
  debt_to_income,
  amount_to_income,
  credit_history_months,
  existing_defaults,
  trace_id,
  feature_event_id,
  source_event_id,
  materialization_source,
  materialized_at,
  created_at
)
SELECT
  CONCAT('legacy-feature-', md5(application_id || ':' || created_at::text)),
  application_id,
  requested_amount,
  debt_to_income,
  amount_to_income,
  credit_history_months,
  existing_defaults,
  CONCAT('legacy-trace-', md5(application_id || ':' || created_at::text)),
  CONCAT('legacy-feature-event-', md5(application_id || ':' || created_at::text)),
  NULL,
  'legacy_backfill',
  created_at,
  created_at
FROM feature_vectors
ON CONFLICT (materialization_id) DO NOTHING;
