DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM pg_class
    WHERE relname = 'feature_vectors'
      AND relkind = 'r'
  ) THEN
    ALTER TABLE feature_vectors RENAME TO feature_vector_projection_legacy;
  END IF;
END $$;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM pg_class
    WHERE relname = 'feature_vector_projection_legacy'
      AND relkind = 'r'
  ) THEN
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
    FROM feature_vector_projection_legacy
    ON CONFLICT (materialization_id) DO NOTHING;
  END IF;
END $$;

CREATE OR REPLACE VIEW feature_vectors AS
SELECT
  latest.application_id,
  latest.requested_amount,
  latest.debt_to_income,
  latest.amount_to_income,
  latest.credit_history_months,
  latest.existing_defaults,
  latest.materialized_at AS created_at
FROM (
  SELECT DISTINCT ON (application_id)
    application_id,
    requested_amount,
    debt_to_income,
    amount_to_income,
    credit_history_months,
    existing_defaults,
    materialized_at,
    created_at,
    materialization_id
  FROM feature_vector_history
  ORDER BY application_id, materialized_at DESC, created_at DESC, materialization_id DESC
) AS latest;
