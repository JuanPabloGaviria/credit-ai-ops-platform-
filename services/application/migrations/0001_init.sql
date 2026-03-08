CREATE TABLE IF NOT EXISTS applications (
  application_id TEXT PRIMARY KEY,
  applicant_id TEXT NOT NULL,
  monthly_income NUMERIC NOT NULL,
  monthly_debt NUMERIC NOT NULL,
  requested_amount NUMERIC NOT NULL,
  credit_history_months INTEGER NOT NULL,
  existing_defaults INTEGER NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS application_outbox (
  event_id TEXT PRIMARY KEY,
  event_name TEXT NOT NULL,
  payload JSONB NOT NULL,
  published_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS application_inbox (
  event_id TEXT PRIMARY KEY,
  event_name TEXT NOT NULL,
  trace_id TEXT NOT NULL,
  payload JSONB NOT NULL,
  received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
