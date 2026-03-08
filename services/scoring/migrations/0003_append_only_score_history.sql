CREATE TABLE IF NOT EXISTS score_prediction_history (
  prediction_id TEXT PRIMARY KEY,
  application_id TEXT NOT NULL,
  requested_amount NUMERIC NOT NULL,
  risk_score NUMERIC NOT NULL,
  model_version TEXT NOT NULL,
  reason_codes TEXT[] NOT NULL,
  trace_id TEXT NOT NULL,
  scoring_event_id TEXT NOT NULL UNIQUE,
  source_event_id TEXT,
  scoring_source TEXT NOT NULL
    CHECK (scoring_source IN ('manual_request', 'feature_event', 'legacy_backfill')),
  scored_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_score_prediction_history_application
  ON score_prediction_history (application_id, scored_at DESC);

CREATE INDEX IF NOT EXISTS idx_score_prediction_history_trace
  ON score_prediction_history (trace_id, scored_at DESC);

INSERT INTO score_prediction_history (
  prediction_id,
  application_id,
  requested_amount,
  risk_score,
  model_version,
  reason_codes,
  trace_id,
  scoring_event_id,
  source_event_id,
  scoring_source,
  scored_at,
  created_at
)
SELECT
  CONCAT('legacy-score-', md5(application_id || ':' || created_at::text)),
  application_id,
  requested_amount,
  risk_score,
  model_version,
  reason_codes,
  CONCAT('legacy-trace-', md5(application_id || ':' || created_at::text)),
  CONCAT('legacy-score-event-', md5(application_id || ':' || created_at::text)),
  NULL,
  'legacy_backfill',
  created_at,
  created_at
FROM score_predictions
ON CONFLICT (prediction_id) DO NOTHING;
