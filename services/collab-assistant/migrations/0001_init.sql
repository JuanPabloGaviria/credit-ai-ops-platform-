CREATE TABLE IF NOT EXISTS assistant_summaries (
  summary_id TEXT PRIMARY KEY,
  application_id TEXT NOT NULL,
  mode TEXT NOT NULL,
  summary TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
