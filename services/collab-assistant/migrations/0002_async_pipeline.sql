DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM pg_class
    WHERE relname = 'assistant_summaries'
      AND relkind = 'r'
  ) THEN
    ALTER TABLE assistant_summaries
      ADD COLUMN IF NOT EXISTS decision TEXT,
      ADD COLUMN IF NOT EXISTS risk_score NUMERIC,
      ADD COLUMN IF NOT EXISTS reason_codes TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
      ADD COLUMN IF NOT EXISTS trace_id TEXT,
      ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

    CREATE UNIQUE INDEX IF NOT EXISTS idx_assistant_summaries_application_id
      ON assistant_summaries (application_id);
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS assistant_outbox (
  event_id TEXT PRIMARY KEY,
  event_name TEXT NOT NULL,
  payload JSONB NOT NULL,
  published_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_assistant_outbox_unpublished
  ON assistant_outbox (created_at ASC)
  WHERE published_at IS NULL;

CREATE TABLE IF NOT EXISTS assistant_inbox (
  event_id TEXT PRIMARY KEY,
  event_name TEXT NOT NULL,
  trace_id TEXT NOT NULL,
  payload JSONB NOT NULL,
  received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
