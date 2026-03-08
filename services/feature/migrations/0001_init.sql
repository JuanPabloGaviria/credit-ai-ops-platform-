CREATE TABLE IF NOT EXISTS feature_vectors (
  application_id TEXT PRIMARY KEY,
  requested_amount NUMERIC NOT NULL,
  debt_to_income NUMERIC NOT NULL,
  amount_to_income NUMERIC NOT NULL,
  credit_history_months INTEGER NOT NULL,
  existing_defaults INTEGER NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS feature_outbox (
  event_id TEXT PRIMARY KEY,
  event_name TEXT NOT NULL,
  payload JSONB NOT NULL,
  published_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS feature_inbox (
  event_id TEXT PRIMARY KEY,
  event_name TEXT NOT NULL,
  trace_id TEXT NOT NULL,
  payload JSONB NOT NULL,
  received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
