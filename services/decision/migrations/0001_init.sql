CREATE TABLE IF NOT EXISTS credit_decisions (
  application_id TEXT PRIMARY KEY,
  decision TEXT NOT NULL,
  reason_codes TEXT[] NOT NULL,
  override_user TEXT,
  override_reason TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS decision_outbox (
  event_id TEXT PRIMARY KEY,
  event_name TEXT NOT NULL,
  payload JSONB NOT NULL,
  published_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS decision_inbox (
  event_id TEXT PRIMARY KEY,
  event_name TEXT NOT NULL,
  trace_id TEXT NOT NULL,
  payload JSONB NOT NULL,
  received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
