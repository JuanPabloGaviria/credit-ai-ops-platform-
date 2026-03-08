DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM pg_class
    WHERE relname = 'applications'
      AND relkind = 'r'
  ) THEN
    ALTER TABLE applications RENAME TO application_projection_legacy;
  END IF;
END $$;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM pg_class
    WHERE relname = 'application_projection_legacy'
      AND relkind = 'r'
  ) THEN
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
    FROM application_projection_legacy
    ON CONFLICT (submission_id) DO NOTHING;
  END IF;
END $$;

CREATE OR REPLACE VIEW applications AS
SELECT
  latest.application_id,
  latest.applicant_id,
  latest.monthly_income,
  latest.monthly_debt,
  latest.requested_amount,
  latest.credit_history_months,
  latest.existing_defaults,
  latest.submitted_at AS created_at
FROM (
  SELECT DISTINCT ON (application_id)
    application_id,
    applicant_id,
    monthly_income,
    monthly_debt,
    requested_amount,
    credit_history_months,
    existing_defaults,
    submitted_at,
    created_at,
    submission_id
  FROM application_submissions
  ORDER BY application_id, submitted_at DESC, created_at DESC, submission_id DESC
) AS latest;
