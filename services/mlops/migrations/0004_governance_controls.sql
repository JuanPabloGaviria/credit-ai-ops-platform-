ALTER TABLE ml_training_runs
  ADD COLUMN IF NOT EXISTS algorithm TEXT,
  ADD COLUMN IF NOT EXISTS feature_spec_ref TEXT,
  ADD COLUMN IF NOT EXISTS training_spec_ref TEXT;

UPDATE ml_training_runs
SET
  algorithm = COALESCE(algorithm, 'sklearn_logistic_regression'),
  feature_spec_ref = COALESCE(feature_spec_ref, 'credit-feature-spec/v1'),
  training_spec_ref = COALESCE(training_spec_ref, 'credit-training-spec/v1');

ALTER TABLE ml_training_runs
  ALTER COLUMN algorithm SET NOT NULL,
  ALTER COLUMN feature_spec_ref SET NOT NULL,
  ALTER COLUMN training_spec_ref SET NOT NULL;

ALTER TABLE model_registry
  ADD COLUMN IF NOT EXISTS feature_spec_ref TEXT,
  ADD COLUMN IF NOT EXISTS training_spec_ref TEXT,
  ADD COLUMN IF NOT EXISTS algorithm TEXT,
  ADD COLUMN IF NOT EXISTS artifact_uri TEXT,
  ADD COLUMN IF NOT EXISTS artifact_digest TEXT;

UPDATE model_registry
SET
  feature_spec_ref = COALESCE(feature_spec_ref, 'credit-feature-spec/v1'),
  training_spec_ref = COALESCE(training_spec_ref, 'credit-training-spec/v1'),
  algorithm = COALESCE(algorithm, 'sklearn_logistic_regression'),
  artifact_uri = COALESCE(artifact_uri, ''),
  artifact_digest = COALESCE(artifact_digest, '');

ALTER TABLE model_registry
  ALTER COLUMN feature_spec_ref SET NOT NULL,
  ALTER COLUMN training_spec_ref SET NOT NULL,
  ALTER COLUMN algorithm SET NOT NULL;

CREATE TABLE IF NOT EXISTS model_stage_assignments (
  event_id TEXT PRIMARY KEY,
  model_name TEXT NOT NULL,
  model_version TEXT NOT NULL,
  stage TEXT NOT NULL CHECK (stage IN ('staging', 'production')),
  approved_by TEXT NOT NULL,
  approval_ticket TEXT NOT NULL,
  risk_signoff_ref TEXT NOT NULL,
  promoted_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT fk_model_stage_assignment_registry
    FOREIGN KEY (model_name, model_version)
    REFERENCES model_registry(model_name, model_version)
    ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_model_stage_assignments_approval_ticket
  ON model_stage_assignments (approval_ticket);

CREATE INDEX IF NOT EXISTS idx_model_stage_assignments_lookup
  ON model_stage_assignments (model_name, model_version, stage, promoted_at DESC);

INSERT INTO model_stage_assignments (
  event_id,
  model_name,
  model_version,
  stage,
  approved_by,
  approval_ticket,
  risk_signoff_ref,
  promoted_at,
  created_at
)
SELECT
  CONCAT('legacy-', md5(model_name || ':' || model_version || ':' || stage || ':' || promoted_at::text)),
  model_name,
  model_version,
  stage,
  'legacy-backfill',
  CONCAT('legacy-ticket-', md5(model_name || ':' || model_version || ':' || stage || ':' || promoted_at::text)),
  'legacy-risk-signoff',
  promoted_at,
  created_at
FROM model_registry
WHERE stage IS NOT NULL
  AND promoted_at IS NOT NULL
ON CONFLICT (event_id) DO NOTHING;
