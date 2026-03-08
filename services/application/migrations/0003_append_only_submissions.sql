CREATE TABLE IF NOT EXISTS application_submissions (
  submission_id TEXT PRIMARY KEY,
  application_id TEXT NOT NULL,
  applicant_id TEXT NOT NULL,
  monthly_income NUMERIC NOT NULL,
  monthly_debt NUMERIC NOT NULL,
  requested_amount NUMERIC NOT NULL,
  credit_history_months INTEGER NOT NULL,
  existing_defaults INTEGER NOT NULL,
  trace_id TEXT NOT NULL,
  intake_event_id TEXT NOT NULL UNIQUE,
  submitted_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_application_submissions_lookup
  ON application_submissions (application_id, submitted_at DESC);

CREATE INDEX IF NOT EXISTS idx_application_submissions_trace
  ON application_submissions (trace_id, submitted_at DESC);

INSERT INTO application_submissions (
  submission_id,
  application_id,
  applicant_id,
  monthly_income,
  monthly_debt,
  requested_amount,
  credit_history_months,
  existing_defaults,
  trace_id,
  intake_event_id,
  submitted_at,
  created_at
)
SELECT
  CONCAT('legacy-submission-', md5(application_id || ':' || created_at::text)),
  application_id,
  applicant_id,
  monthly_income,
  monthly_debt,
  requested_amount,
  credit_history_months,
  existing_defaults,
  CONCAT('legacy-trace-', md5(application_id)),
  CONCAT('legacy-event-', md5(application_id || ':' || created_at::text)),
  created_at,
  created_at
FROM applications
ON CONFLICT (submission_id) DO NOTHING;
