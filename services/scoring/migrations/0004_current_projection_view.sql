DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM pg_class
    WHERE relname = 'score_predictions'
      AND relkind = 'r'
  ) THEN
    ALTER TABLE score_predictions RENAME TO score_prediction_projection_legacy;
  END IF;
END $$;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM pg_class
    WHERE relname = 'score_prediction_projection_legacy'
      AND relkind = 'r'
  ) THEN
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
    FROM score_prediction_projection_legacy
    ON CONFLICT (prediction_id) DO NOTHING;
  END IF;
END $$;

CREATE OR REPLACE VIEW score_predictions AS
SELECT
  latest.application_id,
  latest.requested_amount,
  latest.risk_score,
  latest.model_version,
  latest.reason_codes,
  latest.scored_at AS created_at
FROM (
  SELECT DISTINCT ON (application_id)
    application_id,
    requested_amount,
    risk_score,
    model_version,
    reason_codes,
    scored_at,
    created_at,
    prediction_id
  FROM score_prediction_history
  ORDER BY application_id, scored_at DESC, created_at DESC, prediction_id DESC
) AS latest;
