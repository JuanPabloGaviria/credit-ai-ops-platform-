DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM pg_class
    WHERE relname = 'credit_decisions'
      AND relkind = 'r'
  ) THEN
    ALTER TABLE credit_decisions RENAME TO credit_decision_projection_legacy;
  END IF;
END $$;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM pg_class
    WHERE relname = 'credit_decision_projection_legacy'
      AND relkind = 'r'
  ) THEN
    INSERT INTO credit_decision_history (
      decision_id,
      application_id,
      risk_score,
      decision,
      reason_codes,
      score_model_version,
      decision_source,
      trace_id,
      decision_event_id,
      decided_at,
      created_at
    )
    SELECT
      CONCAT('legacy-decision-', md5(application_id || ':' || COALESCE(updated_at, created_at)::text)),
      application_id,
      risk_score,
      decision,
      reason_codes,
      'legacy_projection',
      'legacy_backfill',
      CONCAT('legacy-trace-', md5(application_id)),
      CONCAT('legacy-event-', md5(application_id || ':' || COALESCE(updated_at, created_at)::text)),
      COALESCE(updated_at, created_at),
      created_at
    FROM credit_decision_projection_legacy
    ON CONFLICT (decision_id) DO NOTHING;
  END IF;
END $$;

CREATE OR REPLACE VIEW credit_decisions AS
SELECT
  latest.application_id,
  latest.decision,
  latest.reason_codes,
  NULL::TEXT AS override_user,
  NULL::TEXT AS override_reason,
  latest.risk_score,
  latest.decided_at AS created_at,
  latest.decided_at AS updated_at
FROM (
  SELECT DISTINCT ON (application_id)
    application_id,
    decision,
    reason_codes,
    risk_score,
    decided_at,
    created_at,
    decision_id
  FROM credit_decision_history
  ORDER BY application_id, decided_at DESC, created_at DESC, decision_id DESC
) AS latest;
