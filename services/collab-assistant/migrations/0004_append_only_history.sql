CREATE TABLE IF NOT EXISTS assistant_summary_history (
  summary_id TEXT PRIMARY KEY,
  application_id TEXT NOT NULL,
  mode TEXT NOT NULL,
  summary TEXT NOT NULL,
  decision TEXT NOT NULL,
  risk_score NUMERIC NOT NULL,
  reason_codes TEXT[] NOT NULL,
  trace_id TEXT NOT NULL,
  source_event_id TEXT,
  summary_event_id TEXT NOT NULL UNIQUE,
  summary_source TEXT NOT NULL
    CHECK (summary_source IN ('manual_request', 'decision_event', 'legacy_backfill')),
  summarized_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_assistant_summary_history_application
  ON assistant_summary_history (application_id, summarized_at DESC);

CREATE INDEX IF NOT EXISTS idx_assistant_summary_history_trace
  ON assistant_summary_history (trace_id, summarized_at DESC);

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM pg_class
    WHERE relname = 'assistant_summaries'
      AND relkind = 'r'
  ) THEN
    ALTER TABLE assistant_summaries RENAME TO assistant_summary_projection_legacy;
  END IF;
END $$;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM pg_class
    WHERE relname = 'assistant_summary_projection_legacy'
      AND relkind = 'r'
  ) THEN
    INSERT INTO assistant_summary_history (
      summary_id,
      application_id,
      mode,
      summary,
      decision,
      risk_score,
      reason_codes,
      trace_id,
      source_event_id,
      summary_event_id,
      summary_source,
      summarized_at,
      created_at
    )
    SELECT
      summary_id,
      application_id,
      mode,
      summary,
      COALESCE(decision, 'review'),
      COALESCE(risk_score, 0),
      reason_codes,
      COALESCE(trace_id, CONCAT('legacy-trace-', md5(application_id || ':' || created_at::text))),
      NULL,
      CONCAT('legacy-summary-event-', md5(application_id || ':' || COALESCE(updated_at, created_at)::text)),
      'legacy_backfill',
      COALESCE(updated_at, created_at),
      created_at
    FROM assistant_summary_projection_legacy
    ON CONFLICT (summary_id) DO NOTHING;
  END IF;
END $$;

CREATE OR REPLACE VIEW assistant_summaries AS
SELECT
  latest.summary_id,
  latest.application_id,
  latest.mode,
  latest.summary,
  latest.decision,
  latest.risk_score,
  latest.reason_codes,
  latest.trace_id,
  latest.summarized_at AS created_at,
  latest.summarized_at AS updated_at
FROM (
  SELECT DISTINCT ON (application_id)
    summary_id,
    application_id,
    mode,
    summary,
    decision,
    risk_score,
    reason_codes,
    trace_id,
    summarized_at,
    created_at
  FROM assistant_summary_history
  ORDER BY application_id, summarized_at DESC, created_at DESC, summary_id DESC
) AS latest;
